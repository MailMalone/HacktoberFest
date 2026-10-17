import pytest

from pr_triage.maintenance_demo import build_demo
from pr_triage.models import ChangedFile
from pr_triage.radar import compare_patches, patch_evidence


def evidence(patch, additions=2, deletions=0):
    return patch_evidence(
        [ChangedFile(filename="app.py", additions=additions, deletions=deletions, patch=patch)]
    )


def test_matching_ignores_hunk_offsets_but_preserves_code_whitespace():
    a = evidence("@@ -1,0 +1,2 @@\n+if valid:\n+    run()")
    b = evidence("@@ -20,0 +20,2 @@\n+if valid:\n+    run()")
    match = compare_patches(a, b)
    assert match["kind"] == "identical changed lines"
    assert match["similarity"] == 1
    assert compare_patches(a, evidence("+if valid:\n+run()")) is None


def test_missing_and_truncated_patches_are_excluded():
    complete = evidence("+a\n+b")
    for partial in (evidence(None), evidence("+a")):
        assert partial["complete"] is False
        assert compare_patches(complete, partial) is None


def test_near_match_exposes_unique_lines_and_counts_repetitions():
    match = compare_patches(evidence("+a\n+b"), evidence("+a\n+b\n+c", 3))
    assert match is None  # 2/3 is below the 70% threshold.
    match = compare_patches(evidence("+a\n+b\n+c", 3), evidence("+a\n+b\n+c\n+d", 4))
    assert match["similarity"] == 0.75
    assert match["right_only"] == [{"file": "app.py", "line": "+d"}]
    assert compare_patches(evidence("+a\n+a"), evidence("+a\n+b")) is None


@pytest.mark.asyncio
async def test_demo_exercises_real_rules_without_external_requests():
    demo = await build_demo()
    assert demo["radar"]["scanned"] == 6
    assert len(demo["radar"]["groups"]) == 2
    assert any(match["exception"] == "Legitimate backport" for match in demo["radar"]["matches"])
    assert [claim["status"] for claim in demo["claims"]["claims"]] == [
        "active",
        "warn",
        "grace",
        "release",
    ]
    assert demo["claims"]["policy"]["enabled"] is False
