"""Shared fixtures for deterministic PR signal and policy tests."""

from datetime import datetime, timezone

import pytest

from pr_triage.models import (
    ContributorSignals,
    DiffSignals,
    EvidencePacket,
    IssueSignals,
    TestSignals,
)


@pytest.fixture
def evidence_packet() -> EvidencePacket:
    """Return a complete, neutral evidence packet."""
    return EvidencePacket(
        pr_number=42,
        pr_title="Add pagination boundary test",
        pr_body="Adds coverage for the final-page cursor behavior.",
        author="octocat",
        repo="octo/example",
        diff=DiffSignals(
            files_changed=2,
            additions=12,
            deletions=2,
            changed_lines=14,
            file_type_counts={"source": 1, "tests": 1},
            patch_coverage_complete=True,
        ),
        tests=TestSignals(tests_changed=True, test_files_changed=1, available=True),
        contributor=ContributorSignals(
            account_age_hours=1000,
            account_new_48h=False,
            prior_pr_count=2,
            first_time_in_repo=False,
            available=True,
        ),
        issue=IssueSignals(linked=False, available=True),
    )


@pytest.fixture
def fixed_now() -> datetime:
    """Return a stable UTC timestamp for account-age tests."""
    return datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
