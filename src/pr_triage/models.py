"""Validated data exchanged between signal extraction and classification."""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonNegativeInt = Annotated[int, Field(ge=0)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Verdict(str, Enum):
    """Allowed first-pass triage outcomes."""

    SUBSTANTIVE = "substantive"
    MINOR = "minor"
    SUSPECTED_SPAM = "suspected-spam"
    NEEDS_REVIEW = "needs-review"


class StrictModel(BaseModel):
    """Reject unexpected fields so evidence packets stay predictable."""

    model_config = ConfigDict(extra="forbid")


class DiffSignals(StrictModel):
    """Computed change statistics. Raw patch text is never serialized."""

    files_changed: NonNegativeInt = 0
    additions: NonNegativeInt = 0
    deletions: NonNegativeInt = 0
    changed_lines: NonNegativeInt = 0
    file_type_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    whitespace_only: bool = False
    patch_coverage_complete: bool = True
    contributor_files_only: bool = False


class TestSignals(StrictModel):
    """Test and continuous-integration changes detected from file paths."""

    tests_changed: bool = False
    test_files_changed: NonNegativeInt = 0
    ci_changed: bool = False
    available: bool = True


class ContributorSignals(StrictModel):
    """Public account age and repository contribution history."""

    account_age_hours: float | None = None
    account_new_48h: bool | None = None
    prior_pr_count: NonNegativeInt | None = None
    first_time_in_repo: bool | None = None
    available: bool = True


class IssueSignals(StrictModel):
    """Linked issue state and labels, without issue body text."""

    linked: bool = False
    issue_number: NonNegativeInt | None = None
    state: str | None = None
    labels: list[str] = Field(default_factory=list)
    available: bool = True


class EvidenceSummary(StrictModel):
    """Local, structured signal values shown with the final recommendation."""

    diff: DiffSignals
    tests: TestSignals
    contributor: ContributorSignals
    issue: IssueSignals


class EvidencePacket(StrictModel):
    """Minimal evidence passed to the model; PR text remains untrusted."""

    pr_number: NonNegativeInt
    pr_title: str
    pr_body: str
    author: str
    repo: str
    diff: DiffSignals
    tests: TestSignals
    contributor: ContributorSignals
    issue: IssueSignals


class ClassificationDecision(StrictModel):
    """Structured model decision before safe, deterministic drafting."""

    verdict: Verdict
    confidence: Confidence
    reasoning: str = Field(min_length=1, max_length=800)
    signals_cited: list[str] = Field(default_factory=list)


class TriageResult(StrictModel):
    """Final verdict plus either a public draft or a private note."""

    pr_number: NonNegativeInt
    pr_title: str
    author: str
    repo: str
    verdict: Verdict
    confidence: Confidence
    reasoning: str = Field(min_length=1, max_length=800)
    signals_cited: list[str] = Field(default_factory=list)
    evidence: EvidenceSummary | None = None
    drafted_comment: str | None = None
    maintainer_note: str


class ChangedFile(StrictModel):
    """Local file metadata used for analysis and specific comment drafts."""

    filename: str
    status: str = "modified"
    additions: NonNegativeInt = 0
    deletions: NonNegativeInt = 0
    patch: str | None = None


class PullRequest(StrictModel):
    """GitHub pull request metadata needed by the CLI."""

    number: NonNegativeInt
    title: str
    body: str = ""
    author: str
    html_url: str = ""
