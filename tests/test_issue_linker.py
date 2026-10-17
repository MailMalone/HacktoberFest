from pr_triage.signals.issue_linker import extract_issue_number


def test_extracts_closing_reference() -> None:
    assert extract_issue_number("Fixes #123", "") == 123


def test_extracts_github_issue_url() -> None:
    assert extract_issue_number("Update parser", "Resolves https://github.com/a/b/issues/9") == 9


def test_unlinked_pr_returns_none() -> None:
    assert extract_issue_number("Improve docs", "No issue linked.") is None
