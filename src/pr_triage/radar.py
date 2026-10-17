"""Compare local patch evidence across PRs without an LLM or code execution."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from itertools import combinations

from pr_triage.github_client import GitHubAPIError, GitHubClient
from pr_triage.models import ChangedFile


def patch_evidence(files: list[ChangedFile]) -> dict:
    """Build a fingerprint only when every changed line is available."""
    rows = []
    complete = bool(files)
    for file in sorted(files, key=lambda item: item.filename):
        additions = deletions = 0
        for line in (file.patch or "").splitlines():
            if line.startswith(("@@", "\\")):
                continue
            if line.startswith(("+", "-")):
                additions += line.startswith("+")
                deletions += line.startswith("-")
                rows.append((file.filename, file.status, line))
        complete &= additions == file.additions and deletions == file.deletions
        complete &= file.patch is not None
    fingerprint = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    return {"complete": complete, "rows": rows, "fingerprint": fingerprint}


def compare_patches(left: dict, right: dict) -> dict | None:
    """Return explainable overlap; never infer misconduct from similarity."""
    if not left["complete"] or not right["complete"] or not left["rows"] or not right["rows"]:
        return None
    a, b = Counter(left["rows"]), Counter(right["rows"])
    common = a & b
    score = sum(common.values()) / sum((a | b).values())
    if score < 0.7:
        return None
    exact = left["fingerprint"] == right["fingerprint"]
    return {
        "kind": "identical changed lines" if exact else "similar changed lines",
        "similarity": round(score, 3),
        "matching_lines": sum(common.values()),
        "left_lines": sum(a.values()),
        "right_lines": sum(b.values()),
        "evidence": [
            {"file": row[0], "left": row[2], "right": row[2]} for row in list(common)[:40]
        ],
        "left_only": [{"file": row[0], "line": row[2]} for row in list(a - b)[:20]],
        "right_only": [{"file": row[0], "line": row[2]} for row in list(b - a)[:20]],
    }


async def scan_radar(client: GitHubClient, repo: str, limit: int = 30) -> dict:
    """Compare up to 100 recently updated open and historical PRs."""
    pulls = await client.list_recent_pull_requests(repo, limit)
    candidates = []
    warnings = []
    for pull in pulls:
        try:
            evidence = patch_evidence(await client.list_pull_request_files(repo, pull["number"]))
            if not evidence["complete"]:
                warnings.append(f"#{pull['number']}: incomplete patch; excluded from matching.")
            candidates.append((pull, evidence))
        except GitHubAPIError as exc:
            warnings.append(f"#{pull['number']}: {exc}")
    matches = []
    candidates.sort(key=lambda pair: pair[0]["number"])
    for (left, a), (right, b) in combinations(candidates, 2):
        match = compare_patches(a, b)
        if match:
            key = hashlib.sha256(
                f"{repo.lower()}:{left['number']}:{a['fingerprint']}:{right['number']}:{b['fingerprint']}".encode()
            ).hexdigest()
            matches.append({"left": left, "right": right, "key": key, **match})
    # Connected components group matches, while the pair evidence remains inspectable.
    groups = []
    for match in matches:
        numbers = {match["left"]["number"], match["right"]["number"]}
        joined = [group for group in groups if group & numbers]
        for group in joined:
            numbers |= group
            groups.remove(group)
        groups.append(numbers)
    return {
        "repo": repo,
        "scanned": len(pulls),
        "matches": matches,
        "groups": [sorted(group) for group in groups],
        "warnings": warnings,
        "scope": f"Up to {limit} most recently updated PRs, including closed and merged PRs.",
    }
