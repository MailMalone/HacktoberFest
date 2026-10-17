"""Call Gemini for a structured decision, then apply deterministic safety policy."""

import re

from google import genai
from google.genai import types

from pr_triage.classifier.prompts import SYSTEM_PROMPT, contains_prompt_injection
from pr_triage.models import ClassificationDecision, EvidencePacket, Verdict

ALLOWED_SIGNALS = {
    "pr_title",
    "pr_body",
    "diff.files_changed",
    "diff.additions",
    "diff.deletions",
    "diff.changed_lines",
    "diff.file_type_counts",
    "diff.whitespace_only",
    "diff.contributor_files_only",
    "tests.tests_changed",
    "tests.test_files_changed",
    "tests.ci_changed",
    "contributor.account_new_48h",
    "contributor.prior_pr_count",
    "issue.linked",
    "issue.state",
    "issue.labels",
}


class ClassificationError(RuntimeError):
    """Raised when Gemini cannot provide a valid structured decision."""


def _bounded_reasoning(text: str) -> str:
    """Keep explanations within the structured output limit."""
    return text if len(text) <= 800 else f"{text[:797]}..."


def evidence_is_incomplete(packet: EvidencePacket) -> bool:
    """Return true when one required signal source could not be collected."""
    return (
        not packet.diff.patch_coverage_complete
        or not packet.tests.available
        or not packet.contributor.available
        or not packet.issue.available
    )


def spam_patterns(packet: EvidencePacket) -> list[str]:
    """Return only the multi-signal spam patterns defined by project policy."""
    patterns: list[str] = []
    contributor = packet.contributor
    if (
        packet.diff.whitespace_only
        and not packet.issue.linked
        and contributor.account_new_48h is True
    ):
        patterns.append("whitespace + no linked issue + account created within 48 hours")

    generic_body = len(packet.pr_body.strip()) < 20 or bool(
        re.fullmatch(
            r"(?:please\s+)?(?:review|merge|fix(?:ed)?|update|changes?\s+made)[.!\s]*",
            packet.pr_body.strip(),
            flags=re.IGNORECASE,
        )
    )
    if (
        packet.diff.contributor_files_only
        and generic_body
        and contributor.first_time_in_repo is True
    ):
        patterns.append("contributor-list-only files + generic body + no prior repository PRs")
    return patterns


def has_conflicting_signals(packet: EvidencePacket) -> bool:
    """Identify concrete combinations that require a human decision."""
    if packet.diff.whitespace_only and packet.issue.linked:
        return True
    if packet.diff.contributor_files_only and (
        packet.tests.tests_changed or packet.tests.ci_changed
    ):
        return True
    return (
        packet.contributor.account_new_48h is True
        and not packet.diff.whitespace_only
        and packet.diff.file_type_counts.get("source", 0) > 0
        and not packet.tests.tests_changed
        and not packet.issue.linked
    )


def apply_safety_policy(
    packet: EvidencePacket, decision: ClassificationDecision
) -> ClassificationDecision:
    """Force conservative verdicts when hard policy checks fail."""
    if contains_prompt_injection(packet.pr_title, packet.pr_body):
        return ClassificationDecision(
            verdict=Verdict.NEEDS_REVIEW,
            confidence=1.0,
            reasoning=(
                "PR text contains instructions that attempt to steer classification. "
                "The text was treated as untrusted, so the model verdict is withheld."
            ),
            signals_cited=["pr_title", "pr_body"],
        )

    if evidence_is_incomplete(packet):
        return ClassificationDecision(
            verdict=Verdict.NEEDS_REVIEW,
            confidence=0.0,
            reasoning="One or more required signals could not be fetched or verified.",
            signals_cited=[],
        )

    if has_conflicting_signals(packet):
        return ClassificationDecision(
            verdict=Verdict.NEEDS_REVIEW,
            confidence=decision.confidence,
            reasoning=_bounded_reasoning(
                "Signals conflict or leave an important gap, so maintainer review is needed. "
                f"{decision.reasoning}"
            ),
            signals_cited=decision.signals_cited,
        )

    if decision.confidence < 0.7:
        return ClassificationDecision(
            verdict=Verdict.NEEDS_REVIEW,
            confidence=decision.confidence,
            reasoning=_bounded_reasoning(f"Model confidence is below 0.7. {decision.reasoning}"),
            signals_cited=decision.signals_cited,
        )

    unknown_signals = set(decision.signals_cited) - ALLOWED_SIGNALS
    if unknown_signals or not decision.signals_cited:
        return ClassificationDecision(
            verdict=Verdict.NEEDS_REVIEW,
            confidence=decision.confidence,
            reasoning="Model did not cite verifiable evidence fields. Maintainer review is needed.",
            signals_cited=[],
        )

    if decision.verdict is Verdict.SUSPECTED_SPAM:
        patterns = spam_patterns(packet)
        conflicting_signals = packet.tests.tests_changed or packet.tests.ci_changed
        if decision.confidence < 0.8 or not patterns or conflicting_signals:
            return ClassificationDecision(
                verdict=Verdict.NEEDS_REVIEW,
                confidence=decision.confidence,
                reasoning=_bounded_reasoning(
                    "The suspected-spam verdict did not meet the required combined-signal "
                    "threshold or conflicts with meaningful test/CI changes. Maintainer "
                    "review is needed. "
                    f"{decision.reasoning}"
                ),
                signals_cited=decision.signals_cited,
            )

    return decision


class GeminiClassifier:
    """Use Gemini structured output to classify a minimal evidence packet."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        if not api_key:
            raise ClassificationError("GEMINI_API_KEY is not configured.")
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def classify(self, packet: EvidencePacket) -> ClassificationDecision:
        """Return a policy-checked structured classification."""
        if contains_prompt_injection(packet.pr_title, packet.pr_body):
            return apply_safety_policy(
                packet,
                ClassificationDecision(
                    verdict=Verdict.NEEDS_REVIEW,
                    confidence=1.0,
                    reasoning="Prompt injection detected in untrusted PR text.",
                    signals_cited=["pr_title", "pr_body"],
                ),
            )

        if evidence_is_incomplete(packet):
            return apply_safety_policy(
                packet,
                ClassificationDecision(
                    verdict=Verdict.NEEDS_REVIEW,
                    confidence=0.0,
                    reasoning="One or more required signals could not be fetched or verified.",
                ),
            )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=packet.model_dump_json(exclude_none=False),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ClassificationDecision,
                    temperature=0.1,
                ),
            )
            if not response.text:
                raise ClassificationError("Gemini returned an empty classification.")
            decision = ClassificationDecision.model_validate_json(response.text)
        except ClassificationError:
            raise
        except Exception as exc:
            raise ClassificationError(
                f"Gemini classification failed ({type(exc).__name__})."
            ) from exc
        return apply_safety_policy(packet, decision)


def safe_fallback(reason: str) -> ClassificationDecision:
    """Return the only allowed outcome when AI classification fails."""
    return ClassificationDecision(
        verdict=Verdict.NEEDS_REVIEW,
        confidence=0.0,
        reasoning=reason,
        signals_cited=[],
    )
