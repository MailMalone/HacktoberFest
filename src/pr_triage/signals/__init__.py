"""Deterministic pull request signal extractors."""

from pr_triage.signals.contributor import build_contributor_signals
from pr_triage.signals.diff_analyzer import analyze_diff
from pr_triage.signals.issue_linker import extract_issue_number
from pr_triage.signals.test_detector import detect_tests

__all__ = ["analyze_diff", "build_contributor_signals", "detect_tests", "extract_issue_number"]
