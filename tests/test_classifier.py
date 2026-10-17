from pr_triage.classifier.engine import apply_safety_policy, spam_patterns
from pr_triage.models import ClassificationDecision, Verdict


def test_low_confidence_falls_back_to_review(evidence_packet) -> None:
    decision = ClassificationDecision(
        verdict=Verdict.SUBSTANTIVE,
        confidence=0.69,
        reasoning="Some evidence supports a contribution.",
        signals_cited=["tests.tests_changed"],
    )

    result = apply_safety_policy(evidence_packet, decision)

    assert result.verdict is Verdict.NEEDS_REVIEW


def test_missing_contributor_data_falls_back_to_review(evidence_packet) -> None:
    evidence_packet.contributor.available = False
    decision = ClassificationDecision(
        verdict=Verdict.MINOR,
        confidence=0.95,
        reasoning="Documentation change.",
        signals_cited=["diff.file_type_counts"],
    )

    result = apply_safety_policy(evidence_packet, decision)

    assert result.verdict is Verdict.NEEDS_REVIEW


def test_new_account_code_change_without_tests_needs_review(evidence_packet) -> None:
    evidence_packet.contributor.account_new_48h = True
    evidence_packet.diff.file_type_counts = {"source": 1}
    evidence_packet.tests.tests_changed = False
    decision = ClassificationDecision(
        verdict=Verdict.SUBSTANTIVE,
        confidence=0.95,
        reasoning="The pull request changes source code.",
        signals_cited=["diff.file_type_counts"],
    )

    result = apply_safety_policy(evidence_packet, decision)

    assert result.verdict is Verdict.NEEDS_REVIEW


def test_single_neutral_signal_never_meets_spam_threshold(evidence_packet) -> None:
    evidence_packet.contributor.account_new_48h = True
    evidence_packet.issue.linked = False

    assert spam_patterns(evidence_packet) == []


def test_combined_whitespace_pattern_meets_spam_threshold(evidence_packet) -> None:
    evidence_packet.diff.whitespace_only = True
    evidence_packet.contributor.account_new_48h = True
    evidence_packet.issue.linked = False

    assert len(spam_patterns(evidence_packet)) == 1


def test_spam_verdict_without_required_combination_becomes_review(evidence_packet) -> None:
    decision = ClassificationDecision(
        verdict=Verdict.SUSPECTED_SPAM,
        confidence=0.99,
        reasoning="Small change.",
        signals_cited=["diff.changed_lines"],
    )

    result = apply_safety_policy(evidence_packet, decision)

    assert result.verdict is Verdict.NEEDS_REVIEW
