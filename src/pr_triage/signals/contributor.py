"""Build contributor signals from public GitHub user/search responses."""

from datetime import datetime, timezone
from typing import Any

from pr_triage.models import ContributorSignals


def build_contributor_signals(
    profile: dict[str, Any], prior_pr_count: int, now: datetime | None = None
) -> ContributorSignals:
    """Calculate account age and prior repository PR history."""
    created_at = datetime.fromisoformat(profile["created_at"].replace("Z", "+00:00"))
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    account_age_hours = max(0.0, (current_time - created_at).total_seconds() / 3600)
    return ContributorSignals(
        account_age_hours=round(account_age_hours, 2),
        account_new_48h=account_age_hours <= 48,
        prior_pr_count=max(0, prior_pr_count),
        first_time_in_repo=prior_pr_count == 0,
        available=True,
    )
