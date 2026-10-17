"""Draft respectful output without sending anything to GitHub."""

from pr_triage.models import (
    ClassificationDecision,
    EvidencePacket,
    EvidenceSummary,
    TriageResult,
    Verdict,
)

PUBLIC_VERDICTS = {Verdict.SUBSTANTIVE, Verdict.MINOR}


def _file_summary(filenames: list[str]) -> str:
    """Format at most three concrete paths for a concise comment."""
    unique = list(dict.fromkeys(filenames))
    if not unique:
        return "the changed files"
    shown = ", ".join(f"`{name}`" for name in unique[:3])
    if len(unique) > 3:
        shown += f", and {len(unique) - 3} more"
    return shown


def draft_public_comment(
    decision: ClassificationDecision,
    packet: EvidencePacket,
    changed_files: list[str],
) -> str:
    """Draft a public comment for positive verdicts only."""
    if decision.verdict not in PUBLIC_VERDICTS:
        raise ValueError("Public comments are only allowed for substantive or minor verdicts.")

    files = _file_summary(changed_files)
    if decision.verdict is Verdict.SUBSTANTIVE:
        acknowledgement = f"This PR makes a substantive change in {files}."
        if packet.tests.tests_changed:
            acknowledgement += " It also updates tests."
        else:
            acknowledgement += ""
        if packet.tests.tests_changed:
            suggestion = (
                "Could you add a short note describing the behavior covered by the new tests?"
            )
        else:
            suggestion = f"Could you add a focused regression test for the change in {changed_files[0] if changed_files else 'this PR'}?"
        return (
            f"Hi @{packet.author}, thanks for this PR! 👋\n\n"
            f"{acknowledgement} {suggestion}\n\n"
            "This looks great — really solid contribution! 🎉"
        )

    suggestion = (
        f"a short usage example alongside {changed_files[0] if changed_files else 'this change'}"
    )
    return (
        f"Hi @{packet.author}, thanks for this PR! 👋\n\n"
        f"This is a useful small update to {files}.\n\n"
        f"Nice catch! If you'd like to go further, consider adding {suggestion}."
    )


def draft_result(
    decision: ClassificationDecision,
    packet: EvidencePacket,
    changed_files: list[str],
) -> TriageResult:
    """Create the final result with exactly one audience-appropriate field."""
    evidence = EvidenceSummary(
        diff=packet.diff,
        tests=packet.tests,
        contributor=packet.contributor,
        issue=packet.issue,
    )
    if decision.verdict in PUBLIC_VERDICTS:
        return TriageResult(
            pr_number=packet.pr_number,
            pr_title=packet.pr_title,
            author=packet.author,
            repo=packet.repo,
            verdict=decision.verdict,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            signals_cited=decision.signals_cited,
            evidence=evidence,
            drafted_comment=draft_public_comment(decision, packet, changed_files),
            maintainer_note="",
        )

    if decision.verdict is Verdict.SUSPECTED_SPAM:
        from pr_triage.classifier.engine import spam_patterns

        patterns = "; ".join(spam_patterns(packet))
        note = (
            f"Combined signals triggered the suspected-spam threshold: {patterns}. "
            f"Evidence: {decision.reasoning}"
        )
    else:
        cited = ", ".join(decision.signals_cited) or "no reliable signal fields"
        note = f"The system is uncertain. {decision.reasoning} Signals: {cited}."

    return TriageResult(
        pr_number=packet.pr_number,
        pr_title=packet.pr_title,
        author=packet.author,
        repo=packet.repo,
        verdict=decision.verdict,
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        signals_cited=decision.signals_cited,
        evidence=evidence,
        drafted_comment=None,
        maintainer_note=note,
    )
