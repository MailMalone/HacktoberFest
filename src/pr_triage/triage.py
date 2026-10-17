"""Collect evidence, classify one pull request, and prepare local output."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pr_triage.classifier.engine import (
    ClassificationError,
    OpenRouterClassifier,
    evidence_is_incomplete,
    safe_fallback,
)
from pr_triage.classifier.prompts import contains_prompt_injection
from pr_triage.commenter.drafter import draft_result
from pr_triage.github_client import GitHubAPIError, GitHubClient
from pr_triage.models import (
    ClassificationDecision,
    ContributorSignals,
    DiffSignals,
    EvidencePacket,
    IssueSignals,
    PullRequest,
    TestSignals,
    TriageResult,
    Verdict,
)
from pr_triage.privacy import ensure_data_sharing_acknowledged
from pr_triage.signals import (
    analyze_diff,
    build_contributor_signals,
    detect_tests,
    extract_issue_number,
)


@dataclass(slots=True)
class EvidenceBundle:
    """Evidence packet plus local paths used only when drafting comments."""

    packet: EvidencePacket
    changed_files: list[str]
    unavailable: list[str]


async def collect_evidence(client: GitHubClient, repo: str, pr: PullRequest) -> EvidenceBundle:
    """Fetch PR signals concurrently and mark unavailable sources explicitly."""
    issue_number = extract_issue_number(pr.title, pr.body)
    files_task = asyncio.create_task(client.list_pull_request_files(repo, pr.number))
    profile_task = asyncio.create_task(client.get_contributor_profile(pr.author))
    history_task = asyncio.create_task(client.count_repository_pull_requests(repo, pr.author))
    tasks = [files_task, profile_task, history_task]
    issue_task = None
    if issue_number is not None:
        issue_task = asyncio.create_task(client.get_linked_issue(repo, issue_number))
        tasks.append(issue_task)

    values = await asyncio.gather(*tasks, return_exceptions=True)
    files_value, profile_value, history_value = values[:3]
    unavailable: list[str] = []

    if isinstance(files_value, BaseException):
        files = []
        diff = DiffSignals(patch_coverage_complete=False)
        tests = TestSignals(available=False)
        unavailable.append("pull request files")
    else:
        files = files_value
        diff = analyze_diff(files)
        tests = detect_tests(files)
        if not diff.patch_coverage_complete:
            unavailable.append("complete text patch coverage")

    if isinstance(profile_value, BaseException) or isinstance(history_value, BaseException):
        contributor = ContributorSignals(available=False)
        unavailable.append("contributor history")
    else:
        try:
            contributor = build_contributor_signals(profile_value, history_value)
        except (KeyError, TypeError, ValueError):
            contributor = ContributorSignals(available=False)
            unavailable.append("contributor history")

    if issue_number is None:
        issue = IssueSignals(linked=False, available=True)
    elif issue_task is not None and isinstance(values[3], BaseException):
        issue = IssueSignals(linked=True, issue_number=issue_number, available=False)
        unavailable.append("linked issue")
    else:
        issue_data = values[3]
        issue = IssueSignals(
            linked=True,
            issue_number=issue_number,
            state=issue_data.get("state"),
            labels=[label["name"] for label in issue_data.get("labels", []) if "name" in label],
            available=True,
        )

    packet = EvidencePacket(
        pr_number=pr.number,
        pr_title=pr.title,
        pr_body=pr.body[:2000],
        author=pr.author,
        repo=repo,
        diff=diff,
        tests=tests,
        contributor=contributor,
        issue=issue,
    )
    return EvidenceBundle(
        packet=packet,
        changed_files=[item.filename for item in files],
        unavailable=unavailable,
    )


def unavailable_result(repo: str, number: int, reason: str) -> TriageResult:
    """Return a safe result when core pull request metadata cannot be fetched."""
    return TriageResult(
        pr_number=number,
        pr_title="Pull request metadata unavailable",
        author="unknown",
        repo=repo,
        verdict=Verdict.NEEDS_REVIEW,
        confidence=0.0,
        reasoning=reason,
        signals_cited=[],
        drafted_comment=None,
        maintainer_note=f"The system is uncertain. {reason}",
    )


async def triage_pull_request(
    client: GitHubClient,
    repo: str,
    pr: PullRequest,
    classifier: OpenRouterClassifier | None,
    accept_data_sharing: bool = False,
) -> TriageResult:
    """Analyze one PR; never call a GitHub write endpoint."""
    bundle = await collect_evidence(client, repo, pr)
    packet = bundle.packet

    if contains_prompt_injection(packet.pr_title, packet.pr_body):
        decision = ClassificationDecision(
            verdict=Verdict.NEEDS_REVIEW,
            confidence=1.0,
            reasoning=(
                "PR text contains instructions that attempt to steer classification. "
                "The text was treated as untrusted, so the model verdict is withheld."
            ),
            signals_cited=["pr_title", "pr_body"],
        )
    elif evidence_is_incomplete(packet):
        detail = ", ".join(bundle.unavailable) or "required evidence"
        decision = safe_fallback(f"Required evidence is incomplete: {detail}.")
    elif classifier is None:
        decision = safe_fallback("OPENROUTER_API_KEY is missing; no AI classification was run.")
    else:
        ensure_data_sharing_acknowledged(accept_data_sharing)
        try:
            decision = await classifier.classify(packet)
        except ClassificationError as exc:
            decision = safe_fallback(str(exc))

    return draft_result(decision, packet, bundle.changed_files)


async def triage_many(
    repo: str,
    pr_numbers: list[int],
    token: str | None,
    api_key: str | None,
    model: str,
    accept_data_sharing: bool = False,
) -> list[TriageResult]:
    """Fetch and classify a selected set of PRs, continuing after individual failures."""
    results: list[TriageResult] = []
    classifier = OpenRouterClassifier(api_key=api_key, model=model) if api_key else None
    async with GitHubClient(token=token) as client:
        for number in pr_numbers:
            try:
                pr = await client.get_pull_request(repo, number)
                result = await triage_pull_request(
                    client,
                    repo,
                    pr,
                    classifier,
                    accept_data_sharing=accept_data_sharing,
                )
            except GitHubAPIError as exc:
                result = unavailable_result(repo, number, str(exc))
            results.append(result)
    return results


async def triage_recent(
    repo: str,
    limit: int,
    token: str | None,
    api_key: str | None,
    model: str,
    accept_data_sharing: bool = False,
) -> list[TriageResult]:
    """List recent open PRs, then classify each independently."""
    classifier = OpenRouterClassifier(api_key=api_key, model=model) if api_key else None
    async with GitHubClient(token=token) as client:
        prs = await client.list_open_pull_requests(repo, limit)
        results: list[TriageResult] = []
        for pr in prs:
            try:
                results.append(
                    await triage_pull_request(
                        client,
                        repo,
                        pr,
                        classifier,
                        accept_data_sharing=accept_data_sharing,
                    )
                )
            except GitHubAPIError as exc:
                results.append(unavailable_result(repo, pr.number, str(exc)))
        return results
