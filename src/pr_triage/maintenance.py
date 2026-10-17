"""Persistent, opt-in claim leases with warnings, fresh checks, and audit history."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pr_triage.github_client import GitHubAPIError, GitHubClient

MONITOR_LOCK = threading.RLock()


class ClaimPolicy(BaseModel):
    """Explicit repository-level authorization and inactivity thresholds."""

    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    idle_days: int = Field(default=7, ge=1, le=365)
    grace_days: int = Field(default=2, ge=1, le=30)
    claim_label: str = Field(default="triage:claim", min_length=1, max_length=50)
    exempt_label: str = Field(default="triage:keep-open", min_length=1, max_length=50)


class StateStore:
    """Keep non-secret policies, warning receipts, exceptions, and an audit log."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".pr-triage" / "maintenance.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, repo TEXT, time TEXT, action TEXT, detail TEXT);
            """)

    def connect(self) -> sqlite3.Connection:
        """Open a short-lived connection suitable for server threads."""
        return sqlite3.connect(self.path, timeout=10)

    def get(self, key: str, default=None):
        """Read one JSON value."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        """Atomically persist a value without storing API credentials."""
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def policies(self) -> list[tuple[str, ClaimPolicy]]:
        """Load registered repository policies."""
        with self.connect() as db:
            rows = db.execute("SELECT key, value FROM state WHERE key LIKE 'policy:%'").fetchall()
        return [(key[7:], ClaimPolicy.model_validate_json(value)) for key, value in rows]

    def log(self, repo: str, action: str, detail: str) -> None:
        """Append an audit record after each action or failure."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(repo,time,action,detail) VALUES (?,?,?,?)",
                (
                    repo,
                    datetime.now(timezone.utc).isoformat(),
                    action,
                    detail,
                ),
            )

    def history(self, repo: str) -> list[dict]:
        """Return the latest 100 action receipts for a repository."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT time,action,detail FROM audit WHERE repo=? ORDER BY id DESC LIMIT 100",
                (repo,),
            ).fetchall()
        return [dict(zip(("time", "action", "detail"), row)) for row in rows]


def timestamp(value: str) -> datetime:
    """Parse GitHub UTC timestamps; invalid dates prevent automatic actions."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Activity timestamp needs a timezone.")
    return result


def local_issue_links(repo: str, body: str) -> set[int]:
    """Recognize local issue references without interpreting foreign repo numbers."""
    numbers = {
        int(n)
        for n in re.findall(
            r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)\b", body, re.IGNORECASE
        )
    }
    numbers.update(
        int(n)
        for n in re.findall(
            rf"https://github\.com/{re.escape(repo)}/issues/(\d+)\b", body, re.IGNORECASE
        )
    )
    return numbers


def lease_decision(
    last_activity: datetime, warning: dict | None, policy: ClaimPolicy, now: datetime
) -> str:
    """Classify a lease independently of network or mutable PR descriptions."""
    if now - last_activity < timedelta(days=policy.idle_days):
        return "active"
    if not warning or last_activity > timestamp(warning["activity"]):
        return "warn"
    if now - timestamp(warning["warned_at"]) >= timedelta(days=policy.grace_days):
        return "release"
    return "grace"


async def _activity(
    client: GitHubClient, repo: str, item: dict, claimant: str, is_pr: bool
) -> datetime:
    """Track claimant replies, assignments, reviews, and commits; ignore bot reminders."""
    base = client._repo_path(repo)
    number = item["number"]
    dates = [timestamp(item["created_at"])]
    comments = await client.paged(f"{base}/issues/{number}/comments")
    for comment in comments:
        if comment["user"][
            "login"
        ].lower() == claimant.lower() and "<!-- pr-triage-lease:" not in comment.get("body", ""):
            dates.append(timestamp(comment.get("updated_at") or comment["created_at"]))
    if is_pr:
        commits = await client.paged(f"{base}/pulls/{number}/commits")
        if len(commits) != item["commits"]:
            raise GitHubAPIError("Incomplete commit history; automatic actions withheld.")
        for commit in commits:
            dates.append(timestamp(commit["commit"]["committer"]["date"]))
        for endpoint in ("comments", "reviews"):
            for reply in await client.paged(f"{base}/pulls/{number}/{endpoint}"):
                if reply["user"]["login"].lower() == claimant.lower():
                    date = (
                        reply.get("updated_at")
                        or reply.get("submitted_at")
                        or reply.get("created_at")
                    )
                    if date:
                        dates.append(timestamp(date))
    for event in await client.paged(f"{base}/issues/{number}/events"):
        if event.get("event") == "reopened" or (
            event.get("event") == "assigned"
            and event.get("assignee", {}).get("login", "").lower() == claimant.lower()
        ):
            dates.append(timestamp(event["created_at"]))
    return max(dates)


async def claim_snapshot(
    client: GitHubClient,
    repo: str,
    policy: ClaimPolicy,
    store: StateStore,
    now: datetime,
    issues_only: bool = False,
) -> list[dict]:
    """Build complete evidence for assigned issues and explicitly labeled PR claims."""
    base = client._repo_path(repo)
    items = await client.paged(f"{base}/issues", {"state": "open"})
    issues = {item["number"]: item for item in items if "pull_request" not in item}
    prs = []
    for item in items:
        if "pull_request" in item:
            if issues_only:
                linked = local_issue_links(repo, f"{item['title']}\n{item.get('body') or ''}")
                if not any(
                    item["user"]["login"].lower() == assignee["login"].lower()
                    for number in linked
                    for assignee in issues.get(number, {}).get("assignees", [])
                ):
                    continue
            prs.append(await client._get(f"{base}/pulls/{item['number']}"))
    snapshots = []
    for item in items:
        is_pr = "pull_request" in item
        if issues_only and is_pr:
            continue
        if is_pr:
            item = next(pr for pr in prs if pr["number"] == item["number"])
            if item["state"] != "open" or item.get("merged"):
                continue
        labels = {label["name"] for label in item.get("labels", [])}
        if policy.exempt_label in labels:
            continue
        claimant_names = (
            [item["user"]["login"]] if is_pr else [a["login"] for a in item.get("assignees", [])]
        )
        links = (
            local_issue_links(repo, f"{item['title']}\n{item.get('body') or ''}")
            if is_pr
            else set()
        )
        if is_pr and any(
            policy.exempt_label in {label["name"] for label in issues.get(n, {}).get("labels", [])}
            for n in links
        ):
            continue
        if (
            is_pr
            and policy.claim_label not in labels
            and not any(
                item["user"]["login"].lower() == a["login"].lower()
                for number in links
                for a in issues.get(number, {}).get("assignees", [])
            )
        ):
            continue
        for claimant in claimant_names:
            activity = await _activity(client, repo, item, claimant, is_pr)
            revision = item.get("head", {}).get("sha") if is_pr else None
            if not is_pr:
                revision = json.dumps(
                    sorted(
                        (pr["number"], pr["head"]["sha"])
                        for pr in prs
                        if pr["user"]["login"].lower() == claimant.lower()
                        and item["number"]
                        in local_issue_links(repo, f"{pr['title']}\n{pr.get('body') or ''}")
                    )
                )
            key = f"lease:{repo.lower()}:{'pr' if is_pr else 'issue'}:{item['number']}:{claimant.lower()}"
            previous = store.get(key, {})
            # Keep an observed head-change receipt so rebased old-dated commits reset the timer.
            observed = previous.get("observed")
            changed_head = bool(previous.get("revision") and previous["revision"] != revision)
            if not is_pr and changed_head:
                before = dict(json.loads(previous["revision"]))
                after = dict(json.loads(revision))
                changed_head = any(before.get(number) != sha for number, sha in after.items())
            if changed_head:
                observed = now.isoformat()
            if observed:
                activity = max(activity, timestamp(observed))
            if is_pr:
                for number in links:
                    issue = issues.get(number)
                    if issue and any(
                        a["login"].lower() == claimant.lower() for a in issue.get("assignees", [])
                    ):
                        activity = max(
                            activity, await _activity(client, repo, issue, claimant, False)
                        )
            else:
                for pr in prs:
                    if pr["user"]["login"].lower() == claimant.lower() and item[
                        "number"
                    ] in local_issue_links(repo, f"{pr['title']}\n{pr.get('body') or ''}"):
                        activity = max(activity, await _activity(client, repo, pr, claimant, True))
                        # Protected PRs also protect the associated assignment.
                        if policy.exempt_label in {label["name"] for label in pr.get("labels", [])}:
                            activity = now
            if not previous.get("baseline"):
                # Initial metadata updates may be maintainer activity; err toward allowing more time.
                baseline = item["updated_at"]
            else:
                baseline = previous["baseline"]
            activity = max(activity, timestamp(baseline))
            warning = previous.get("warning")
            snapshots.append(
                {
                    "key": key,
                    "number": item["number"],
                    "title": item["title"],
                    "url": item["html_url"],
                    "claimant": claimant,
                    "kind": "pr" if is_pr else "issue",
                    "last_activity": activity.isoformat(),
                    "idle_days": max(0, (now - activity).days),
                    "status": "needs-review"
                    if previous.get("pending_warning")
                    else lease_decision(activity, warning, policy, now),
                    "warning": warning,
                    "revision": revision,
                    "observed": observed,
                    "baseline": baseline,
                    "action": "close PR" if is_pr else "remove this assignee; keep issue open",
                }
            )
    return snapshots


async def check_claims(
    client: GitHubClient,
    repo: str,
    policy: ClaimPolicy,
    store: StateStore,
    execute: bool = False,
    now: datetime | None = None,
) -> dict:
    """Preview leases or warn/release due claims, rechecking before each release."""
    now = now or datetime.now(timezone.utc)
    if execute and not policy.enabled:
        raise ValueError("Enable this repository's claim policy before executing actions.")
    if execute:
        permissions = (await client._get(client._repo_path(repo))).get("permissions", {})
        if not any(permissions.get(name) for name in ("push", "maintain", "admin")):
            raise ValueError("Claim actions require maintainer access to this repository.")
    claims = await claim_snapshot(client, repo, policy, store, now)
    if execute:
        for claim in claims:
            previous = store.get(claim["key"], {})
            if previous.get("pending_warning"):
                store.log(
                    repo, "needs-review", f"#{claim['number']}: verify pending warning on GitHub"
                )
                continue
            saved = {key: claim[key] for key in ("revision", "observed", "baseline")}
            saved["warning"] = claim["warning"] if claim["status"] != "active" else None
            if claim["status"] == "warn":
                marker = f"<!-- pr-triage-lease:{claim['key']} -->"
                # Persist intent first. An uncertain write is never blindly retried.
                if previous.get("pending_warning"):
                    store.log(
                        repo,
                        "needs-review",
                        f"#{claim['number']}: verify pending warning on GitHub",
                    )
                    continue
                store.set(claim["key"], {**saved, "pending_warning": True})
                store.log(repo, "warning-attempt", f"#{claim['number']} @{claim['claimant']}")
                path = f"{client._repo_path(repo)}/issues/{claim['number']}/comments"
                response = await client.request_json(
                    "POST",
                    path,
                    {
                        "body": (
                            f"@{claim['claimant']}, this claim has had no recorded contributor activity for "
                            f"{policy.idle_days} days. Please reply or push a commit within "
                            f"{policy.grace_days} days if you are continuing. Otherwise we will "
                            f"{claim['action']} so others can help. Maintainers can apply "
                            f"`{policy.exempt_label}` to pause this policy.\n\n{marker}"
                        )
                    },
                )
                saved["warning"] = {
                    "warned_at": now.isoformat(),
                    "activity": claim["last_activity"],
                    "url": response["html_url"],
                }
                store.log(
                    repo,
                    "warned",
                    f"#{claim['number']} @{claim['claimant']}: {response['html_url']}",
                )
            elif claim["status"] == "release":
                fresh = await claim_snapshot(client, repo, policy, store, now)
                current = next((value for value in fresh if value["key"] == claim["key"]), None)
                if current is None or current["status"] != "release":
                    store.log(repo, "skipped", f"#{claim['number']}: activity or claim changed")
                    continue
                store.log(
                    repo,
                    "release-attempt",
                    f"#{claim['number']} @{claim['claimant']}: {claim['action']}",
                )
                if claim["kind"] == "pr":
                    await client.request_json(
                        "PATCH",
                        f"{client._repo_path(repo)}/pulls/{claim['number']}",
                        {"state": "closed"},
                    )
                else:
                    await client.request_json(
                        "DELETE",
                        f"{client._repo_path(repo)}/issues/{claim['number']}/assignees",
                        {"assignees": [claim["claimant"]]},
                    )
                store.log(
                    repo, "released", f"#{claim['number']} @{claim['claimant']}: {claim['action']}"
                )
            store.set(claim["key"], saved)
        claims = await claim_snapshot(client, repo, policy, store, now)
    return {"claims": claims, "policy": policy.model_dump(), "history": store.history(repo)}


async def inactive_issues(client, repo, policy, store, now=None) -> dict:
    """Read GitHub activity and report assigned issues with no recent visible progress."""
    now = now or datetime.now(timezone.utc)
    snapshots = await claim_snapshot(client, repo, policy, store, now, issues_only=True)
    assigned = [claim for claim in snapshots if claim["kind"] == "issue"]
    inactive = []
    for claim in assigned:
        if now - timestamp(claim["last_activity"]) < timedelta(days=policy.idle_days):
            continue
        inactive.append(
            {
                key: claim[key]
                for key in (
                    "number",
                    "title",
                    "url",
                    "claimant",
                    "kind",
                    "last_activity",
                    "idle_days",
                )
            }
            | {"status": "inactive"}
        )
    inactive.sort(key=lambda claim: claim["idle_days"], reverse=True)
    return {
        "claims": inactive,
        "tracked": len(assigned),
        "read_only": True,
        "policy": policy.model_copy(update={"enabled": False}).model_dump(),
        "history": [],
    }
