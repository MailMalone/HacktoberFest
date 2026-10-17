from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from pr_triage.github_client import GitHubAPIError
from pr_triage.maintenance import (
    ClaimPolicy,
    StateStore,
    check_claims,
    claim_snapshot,
    inactive_issues,
    lease_decision,
    local_issue_links,
)

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=10)).isoformat()


@pytest.mark.asyncio
async def test_inactive_scan_ignores_enabled_policy_and_old_pending_warning(tmp_path):
    client = FakeGitHub()
    store = StateStore(tmp_path / "scan.sqlite")
    store.set("lease:owner/repo:issue:1:alice", {"pending_warning": True})
    result = await inactive_issues(client, "owner/repo", ClaimPolicy(enabled=True), store, NOW)
    assert result["read_only"] is True
    assert result["policy"]["enabled"] is False
    assert result["claims"][0]["status"] == "inactive"
    assert result["claims"][0]["idle_days"] == 10
    assert client.writes == []
    client.comments = [
        {"user": {"login": "alice"}, "body": "Progress", "created_at": NOW.isoformat()}
    ]
    assert (await inactive_issues(client, "owner/repo", ClaimPolicy(), store, NOW))["claims"] == []


@pytest.mark.asyncio
async def test_inactive_scan_lists_issues_only(tmp_path):
    client = FakeGitHub(pr=True)
    result = await inactive_issues(
        client, "owner/repo", ClaimPolicy(), StateStore(tmp_path / "scan.sqlite"), NOW
    )
    assert result["claims"] == []
    assert client.writes == []


class FakeGitHub:
    def __init__(self, pr=False):
        self.item = {
            "number": 1,
            "title": "Working on task",
            "body": "",
            "state": "open",
            "html_url": "https://github.com/owner/repo/issues/1",
            "created_at": OLD,
            "updated_at": OLD,
            "user": {"login": "alice"},
            "assignees": [{"login": "alice"}],
            "labels": [],
        }
        if pr:
            self.item.update(
                {
                    "pull_request": {},
                    "commits": 1,
                    "head": {"sha": "abc"},
                    "labels": [{"name": "triage:claim"}],
                }
            )
        self.writes = []
        self.comments = []
        self.fail = False
        self.push = True

    def _repo_path(self, repo):
        return f"/repos/{repo}"

    async def _get(self, path):
        return deepcopy(self.item) if "/pulls/" in path else {"permissions": {"push": self.push}}

    async def paged(self, path, params=None):
        if self.fail:
            raise GitHubAPIError("Evidence missing")
        if path.endswith("/issues"):
            return [deepcopy(self.item)] if self.item["state"] == "open" else []
        if path.endswith("/commits"):
            return [{"commit": {"committer": {"date": OLD}}}]
        if path.endswith("/comments"):
            return deepcopy(self.comments)
        return []

    async def request_json(self, method, path, payload):
        self.writes.append((method, path, payload))
        if method == "PATCH":
            self.item["state"] = "closed"
        if method == "DELETE":
            self.item["assignees"] = []
        return {"html_url": "https://github.com/owner/repo/issues/1#issuecomment-1"}


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state.sqlite")


def test_lease_boundaries_and_activity_restart_grace():
    policy = ClaimPolicy()
    activity = NOW - timedelta(days=7)
    assert lease_decision(activity, None, policy, NOW) == "warn"
    warning = {"activity": activity.isoformat(), "warned_at": NOW.isoformat()}
    assert lease_decision(activity, warning, policy, NOW + timedelta(days=1)) == "grace"
    assert lease_decision(activity, warning, policy, NOW + timedelta(days=2)) == "release"
    assert lease_decision(NOW, warning, policy, NOW + timedelta(days=8)) == "warn"


def test_foreign_issue_links_do_not_enroll_local_issue_numbers():
    assert (
        local_issue_links("owner/repo", "Fixes other/repo#1 https://github.com/other/repo/issues/2")
        == set()
    )
    assert local_issue_links("owner/repo", "Fixes #3 https://github.com/owner/repo/issues/4") == {
        3,
        4,
    }


@pytest.mark.asyncio
async def test_preview_is_read_only_and_paused_policy_cannot_execute(store):
    client = FakeGitHub()
    data = await check_claims(client, "owner/repo", ClaimPolicy(), store, now=NOW)
    assert data["claims"][0]["status"] == "warn"
    assert not client.writes
    with pytest.raises(ValueError, match="Enable"):
        await check_claims(client, "owner/repo", ClaimPolicy(), store, execute=True, now=NOW)


@pytest.mark.asyncio
async def test_warning_persists_restart_then_only_inactive_assignee_is_removed(store):
    client = FakeGitHub()
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    assert len(client.writes) == 1
    assert client.writes[0][0] == "POST"
    reopened_store = StateStore(store.path)
    await check_claims(
        client, "owner/repo", policy, reopened_store, execute=True, now=NOW + timedelta(days=1)
    )
    assert len(client.writes) == 1
    await check_claims(
        client, "owner/repo", policy, reopened_store, execute=True, now=NOW + timedelta(days=2)
    )
    assert client.writes[-1] == (
        "DELETE",
        "/repos/owner/repo/issues/1/assignees",
        {"assignees": ["alice"]},
    )
    assert client.item["state"] == "open"
    assert any(event["action"] == "released" for event in store.history("owner/repo"))


@pytest.mark.asyncio
async def test_contributor_reply_prevents_release_and_bot_reminder_does_not(store):
    client = FakeGitHub(pr=True)
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    client.comments = [
        {"user": {"login": "bot"}, "body": "Reminder", "created_at": NOW.isoformat()}
    ]
    data = await check_claims(client, "owner/repo", policy, store, now=NOW + timedelta(days=2))
    assert data["claims"][0]["status"] == "release"
    client.comments.append(
        {"user": {"login": "alice"}, "body": "Still working", "created_at": NOW.isoformat()}
    )
    data = await check_claims(
        client, "owner/repo", policy, store, execute=True, now=NOW + timedelta(days=2)
    )
    assert data["claims"][0]["status"] == "active"
    assert len(client.writes) == 1


@pytest.mark.asyncio
async def test_head_change_with_old_commit_dates_resets_inactivity(store):
    client = FakeGitHub(pr=True)
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    client.item["head"]["sha"] = "new-sha"
    data = await check_claims(
        client, "owner/repo", policy, store, execute=True, now=NOW + timedelta(days=3)
    )
    assert data["claims"][0]["status"] == "active"
    assert len(client.writes) == 1


@pytest.mark.asyncio
async def test_only_explicit_pr_claims_are_enrolled_and_exemptions_win(store):
    client = FakeGitHub(pr=True)
    client.item["labels"] = []
    assert await claim_snapshot(client, "owner/repo", ClaimPolicy(), store, NOW) == []
    client.item["labels"] = [{"name": "triage:claim"}, {"name": "triage:keep-open"}]
    assert await claim_snapshot(client, "owner/repo", ClaimPolicy(), store, NOW) == []


@pytest.mark.asyncio
async def test_missing_evidence_and_no_maintainer_access_never_mutate(store):
    client = FakeGitHub()
    client.fail = True
    with pytest.raises(GitHubAPIError):
        await check_claims(
            client, "owner/repo", ClaimPolicy(enabled=True), store, execute=True, now=NOW
        )
    assert not client.writes
    client.fail = False
    client.push = False
    with pytest.raises(ValueError, match="maintainer"):
        await check_claims(
            client, "owner/repo", ClaimPolicy(enabled=True), store, execute=True, now=NOW
        )


@pytest.mark.asyncio
async def test_overdue_pr_claim_is_closed_after_grace(store):
    client = FakeGitHub(pr=True)
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    await check_claims(
        client, "owner/repo", policy, store, execute=True, now=NOW + timedelta(days=3)
    )
    assert client.writes[-1] == ("PATCH", "/repos/owner/repo/pulls/1", {"state": "closed"})


@pytest.mark.asyncio
async def test_uncertain_warning_is_not_blindly_retried(store):
    client = FakeGitHub()
    policy = ClaimPolicy(enabled=True)
    claims = await claim_snapshot(client, "owner/repo", policy, store, NOW)
    store.set(claims[0]["key"], {"pending_warning": True})
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    assert not client.writes
    assert store.history("owner/repo")[0]["action"] == "needs-review"


@pytest.mark.asyncio
async def test_release_rechecks_and_skips_claim_removed_between_reads(store, monkeypatch):
    from pr_triage import maintenance

    client = FakeGitHub(pr=True)
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    real_snapshot = maintenance.claim_snapshot
    reads = 0

    async def racing_snapshot(*args):
        nonlocal reads
        reads += 1
        if reads > 1:
            return []
        return await real_snapshot(*args)

    monkeypatch.setattr(maintenance, "claim_snapshot", racing_snapshot)
    await check_claims(
        client, "owner/repo", policy, store, execute=True, now=NOW + timedelta(days=3)
    )
    assert reads == 3  # Initial evidence, immediate recheck, refreshed UI result.
    assert len(client.writes) == 1  # Warning only; no PATCH.
    assert store.history("owner/repo")[0]["action"] == "skipped"


@pytest.mark.asyncio
async def test_incomplete_commit_history_blocks_actions(store):
    client = FakeGitHub(pr=True)
    client.item["commits"] = 300  # Pulls commits API can return only 250.
    with pytest.raises(GitHubAPIError, match="Incomplete commit"):
        await check_claims(
            client, "owner/repo", ClaimPolicy(enabled=True), store, execute=True, now=NOW
        )
    assert not client.writes


@pytest.mark.asyncio
async def test_reopened_claim_gets_new_activity_before_release(store):
    client = FakeGitHub(pr=True)
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    original = client.paged

    async def activity_with_reopen(path, params=None):
        if path.endswith("/events"):
            return [{"event": "reopened", "created_at": NOW.isoformat()}]
        return await original(path, params)

    client.paged = activity_with_reopen
    result = await check_claims(
        client, "owner/repo", policy, store, execute=True, now=NOW + timedelta(days=2)
    )
    assert result["claims"][0]["status"] == "active"
    assert len(client.writes) == 1


class LinkedClaims(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.pr = deepcopy(FakeGitHub(pr=True).item)
        self.pr.update({"number": 2, "body": "Fixes #1"})
        self.pr.pop("pull_request")

    async def _get(self, path):
        if "/pulls/" in path:
            return deepcopy(self.pr)
        return await super()._get(path)

    async def paged(self, path, params=None):
        if path.endswith("/issues"):
            result = [deepcopy(self.item)]
            if self.pr["state"] == "open":
                result.append({**deepcopy(self.pr), "pull_request": {}})
            return result
        return await super().paged(path, params)

    async def request_json(self, method, path, payload):
        if method == "PATCH":
            self.writes.append((method, path, payload))
            self.pr["state"] = "closed"
            return {"state": "closed"}
        return await super().request_json(method, path, payload)


@pytest.mark.asyncio
async def test_active_linked_pr_protects_issue_assignment(store):
    client = LinkedClaims()
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    original = client.paged

    async def recent_commits(path, params=None):
        if path.endswith("/commits"):
            return [{"commit": {"committer": {"date": NOW.isoformat()}}}]
        return await original(path, params)

    client.paged = recent_commits
    data = await check_claims(
        client, "owner/repo", policy, store, execute=True, now=NOW + timedelta(days=2)
    )
    assert {claim["status"] for claim in data["claims"]} == {"active"}
    assert not any(method in {"PATCH", "DELETE"} for method, _, _ in client.writes)


@pytest.mark.asyncio
async def test_stale_linked_claim_releases_assignment_and_closes_only_pr(store):
    client = LinkedClaims()
    policy = ClaimPolicy(enabled=True)
    await check_claims(client, "owner/repo", policy, store, execute=True, now=NOW)
    await check_claims(
        client, "owner/repo", policy, store, execute=True, now=NOW + timedelta(days=3)
    )
    assert any(method == "DELETE" for method, _, _ in client.writes)
    assert ("PATCH", "/repos/owner/repo/pulls/2", {"state": "closed"}) in client.writes
    assert client.item["state"] == "open"


@pytest.mark.asyncio
async def test_protected_issue_also_protects_its_linked_claim_pr(store):
    client = LinkedClaims()
    client.item["labels"] = [{"name": "triage:keep-open"}]
    result = await check_claims(
        client, "owner/repo", ClaimPolicy(enabled=True), store, execute=True, now=NOW
    )
    assert not result["claims"]
    assert not client.writes
