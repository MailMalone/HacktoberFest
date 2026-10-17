from pr_triage.models import ChangedFile
from pr_triage.signals.test_detector import detect_tests


def test_detects_tests_and_ci_files() -> None:
    result = detect_tests(
        [
            ChangedFile(filename="tests/test_api.py"),
            ChangedFile(filename=".github/workflows/ci.yml"),
        ]
    )

    assert result.tests_changed is True
    assert result.test_files_changed == 1
    assert result.ci_changed is True


def test_docs_only_change_has_no_test_signal() -> None:
    result = detect_tests([ChangedFile(filename="docs/guide.md")])

    assert result.tests_changed is False
    assert result.ci_changed is False
