from pr_triage.classifier.engine import apply_safety_policy
from pr_triage.models import ClassificationDecision, Verdict


def test_neutral_small_change_can_stay_minor(evidence_packet) -> None:
    evidence_packet.diff.changed_lines = 2
    evidence_packet.diff.file_type_counts = {"docs": 1}
    evidence_packet.tests.tests_changed = False
    decision = ClassificationDecision(
        verdict=Verdict.MINOR,
        confidence=0.82,
        reasoning="A small documentation update is legitimate.",
        signals_cited=["diff.changed_lines", "diff.file_type_counts"],
    )

    result = apply_safety_policy(evidence_packet, decision)

    assert result.verdict is Verdict.MINOR


def test_first_time_contributor_with_code_and_tests_can_be_substantive(evidence_packet) -> None:
    evidence_packet.contributor.account_new_48h = True
    evidence_packet.diff.file_type_counts = {"source": 1, "tests": 1}
    evidence_packet.tests.tests_changed = True
    decision = ClassificationDecision(
        verdict=Verdict.SUBSTANTIVE,
        confidence=0.9,
        reasoning="The pull request adds code and a regression test.",
        signals_cited=["diff.file_type_counts", "tests.tests_changed"],
    )

    result = apply_safety_policy(evidence_packet, decision)

    assert result.verdict is Verdict.SUBSTANTIVE


def test_spam_needs_high_confidence_and_combined_signals(evidence_packet) -> None:
    evidence_packet.diff.whitespace_only = True
    evidence_packet.contributor.account_new_48h = True
    evidence_packet.issue.linked = False
    evidence_packet.tests.tests_changed = False
    decision = ClassificationDecision(
        verdict=Verdict.SUSPECTED_SPAM,
        confidence=0.85,
        reasoning="Whitespace-only patch, no linked issue, and account is under 48 hours old.",
        signals_cited=[
            "diff.whitespace_only",
            "issue.linked",
            "contributor.account_new_48h",
        ],
    )

    result = apply_safety_policy(evidence_packet, decision)

    assert result.verdict is Verdict.SUSPECTED_SPAM
