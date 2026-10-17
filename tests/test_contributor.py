from pr_triage.signals.contributor import build_contributor_signals


def test_new_account_and_first_repo_contribution(fixed_now) -> None:
    profile = {"created_at": "2026-10-16T12:00:00Z"}

    result = build_contributor_signals(profile, prior_pr_count=0, now=fixed_now)

    assert result.account_age_hours == 24
    assert result.account_new_48h is True
    assert result.first_time_in_repo is True


def test_old_account_is_not_new(fixed_now) -> None:
    profile = {"created_at": "2026-10-01T12:00:00Z"}

    result = build_contributor_signals(profile, prior_pr_count=3, now=fixed_now)

    assert result.account_new_48h is False
    assert result.first_time_in_repo is False
