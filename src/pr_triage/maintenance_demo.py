"""Deterministic local demo fixtures; never calls GitHub or modifies claims."""

from datetime import datetime, timedelta, timezone

from pr_triage.maintenance import ClaimPolicy, lease_decision
from pr_triage.models import ChangedFile
from pr_triage.radar import scan_radar


async def build_demo() -> dict:
    """Exercise real matching and lease rules using six synthetic PRs."""
    patches = {
        1: ("queue.py", "+if not title.strip():\n+    raise ValueError('blank title')"),
        2: ("tests/test_queue.py", "+def test_blank():\n+    assert rejects(' ')"),
        3: (
            "README.md",
            "+## Tools\n+Choose a tool.\n+See the community list.\n+Compare developer offers.",
        ),
        4: (
            "README.md",
            "+## Tools\n+Choose a tool.\n+See the community list.\n+Compare developer offers.",
        ),
        5: (
            "README.md",
            "+## Tools\n+Choose a tool.\n+See the community list.\n+Compare developer offers.\n+Updated resources.",
        ),
        6: ("queue.py", "+if not title.strip():\n+    raise ValueError('blank title')"),
    }
    titles = [
        "Reject blank task titles",
        "Test empty titles",
        "Add contributor resources",
        "Document useful tools",
        "Refresh developer resources",
        "Backport blank-title fix",
    ]

    class DemoClient:
        async def list_recent_pull_requests(self, repo, limit):
            return [
                {
                    "number": n,
                    "title": titles[n - 1],
                    "author": f"demo-user-{n}",
                    "state": "merged" if n == 1 else "open",
                    "url": f"https://github.com/{repo}/pull/{n}",
                }
                for n in patches
            ]

        async def list_pull_request_files(self, repo, number):
            filename, patch = patches[number]
            return [ChangedFile(filename=filename, additions=len(patch.splitlines()), patch=patch)]

    radar = await scan_radar(DemoClient(), "demo/fixtures")
    for match in radar["matches"]:
        match["exception"] = (
            "Legitimate backport"
            if {match["left"]["number"], match["right"]["number"]} == {1, 6}
            else None
        )
    now = datetime.now(timezone.utc)
    policy = ClaimPolicy()
    claims = []
    for number, (idle, warning_days) in enumerate([(1, None), (8, None), (8, 1), (11, 3)], 10):
        activity = now - timedelta(days=idle)
        warning = (
            {
                "activity": activity.isoformat(),
                "warned_at": (now - timedelta(days=warning_days)).isoformat(),
            }
            if warning_days
            else None
        )
        claims.append(
            {
                "number": number,
                "title": "Synthetic claim lifecycle example",
                "url": "https://github.com",
                "claimant": f"demo-user-{number}",
                "kind": "issue",
                "last_activity": activity.isoformat(),
                "idle_days": idle,
                "status": lease_decision(activity, warning, policy, now),
                "warning": warning,
                "action": "remove this assignee; keep issue open",
            }
        )
    return {
        "radar": radar,
        "claims": {"claims": claims, "policy": policy.model_dump(), "history": []},
    }
