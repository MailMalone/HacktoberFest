import pytest

from pr_triage.commenter.drafter import draft_public_comment, draft_result
from pr_triage.models import ClassificationDecision, Verdict


@pytest.mark.parametrize("verdict", [Verdict.SUSPECTED_SPAM, Verdict.NEEDS_REVIEW])
def test_non_positive_verdict_cannot_get_public_comment(evidence_packet, verdict) -> None:
    decision = ClassificationDecision(
        verdict=verdict,
        confidence=0.9,
        reasoning="Maintainer review needed.",
        signals_cited=["diff.whitespace_only"],
    )

    with pytest.raises(ValueError):
        draft_public_comment(decision, evidence_packet, ["src/app.py"])


def test_substantive_result_has_specific_public_comment(evidence_packet) -> None:
    decision = ClassificationDecision(
        verdict=Verdict.SUBSTANTIVE,
        confidence=0.91,
        reasoning="Adds implementation and a regression test.",
        signals_cited=["tests.tests_changed", "diff.file_type_counts"],
    )

    result = draft_result(decision, evidence_packet, ["src/pager.py", "tests/test_pager.py"])

    assert "@octocat" in result.drafted_comment
    assert "`src/pager.py`" in result.drafted_comment
    assert "This looks great" in result.drafted_comment
    assert result.maintainer_note == ""


def test_review_result_has_private_note_only(evidence_packet) -> None:
    decision = ClassificationDecision(
        verdict=Verdict.NEEDS_REVIEW,
        confidence=0.5,
        reasoning="Signals conflict.",
        signals_cited=["diff.files_changed"],
    )

    result = draft_result(decision, evidence_packet, ["README.md"])

    assert result.drafted_comment is None
    assert "system is uncertain" in result.maintainer_note
