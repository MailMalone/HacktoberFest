from pr_triage.models import ChangedFile
from pr_triage.signals.diff_analyzer import analyze_diff


def test_detects_whitespace_only_patch() -> None:
    files = [
        ChangedFile(
            filename="src/app.py",
            additions=1,
            deletions=1,
            patch="@@ -1 +1 @@\n-    \n+\t ",
        )
    ]

    result = analyze_diff(files)

    assert result.whitespace_only is True
    assert result.changed_lines == 2
    assert result.file_type_counts == {"source": 1}


def test_code_change_is_not_whitespace_only() -> None:
    result = analyze_diff(
        [ChangedFile(filename="src/app.py", additions=1, patch="@@ -1 +1 @@\n-return 1\n+return 2")]
    )

    assert result.whitespace_only is False


def test_missing_text_patch_marks_coverage_incomplete() -> None:
    result = analyze_diff([ChangedFile(filename="image.png", additions=1, patch=None)])

    assert result.patch_coverage_complete is False
    assert result.whitespace_only is False


def test_contributor_list_only_is_detected() -> None:
    result = analyze_diff(
        [ChangedFile(filename="CONTRIBUTORS.md", additions=1, patch="@@ -1 +1 @@\n-a\n+b")]
    )

    assert result.contributor_files_only is True
