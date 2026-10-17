"""Compute diff statistics without retaining patch content in model input."""

from collections import Counter
from pathlib import PurePosixPath

from pr_triage.models import ChangedFile, DiffSignals

CONTRIBUTOR_FILES = {
    "contributors.md",
    "contributing.md",
    ".all-contributorsrc",
    "contributors.json",
}


def _category(filename: str) -> str:
    """Map a path to a small, stable file category."""
    path = PurePosixPath(filename.lower())
    if any(part in {"test", "tests", "spec", "specs"} for part in path.parts):
        return "tests"
    if path.name.startswith(("test_", "spec_")) or path.name.endswith(
        ("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js")
    ):
        return "tests"
    if path.suffix in {".md", ".rst", ".txt", ".adoc"} or "docs" in path.parts:
        return "docs"
    if path.suffix in {".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp"}:
        return "source"
    if path.suffix in {".yml", ".yaml", ".toml", ".ini", ".cfg", ".json"}:
        return "config"
    if path.suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}:
        return "assets"
    return path.suffix.lstrip(".") or "other"


def _changed_lines(patch: str | None) -> list[str] | None:
    """Return added/deleted source lines, or None when patch is unavailable."""
    if patch is None:
        return None
    lines: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("+++", "---", "\\")):
            continue
        if line.startswith(("+", "-")):
            lines.append(line[1:])
    return lines


def analyze_diff(files: list[ChangedFile]) -> DiffSignals:
    """Summarize changed files and detect whitespace-only edits."""
    categories = Counter(_category(item.filename) for item in files)
    changed_content: list[str] = []
    coverage_complete = True
    saw_patch_change = False

    for item in files:
        lines = _changed_lines(item.patch)
        if lines is None:
            if item.additions or item.deletions:
                coverage_complete = False
            continue
        changed_content.extend(lines)
        saw_patch_change = saw_patch_change or bool(lines)

    whitespace_only = (
        saw_patch_change
        and coverage_complete
        and bool(changed_content)
        and all(not line.strip() for line in changed_content)
    )
    contributor_only = bool(files) and all(
        PurePosixPath(item.filename.lower()).name in CONTRIBUTOR_FILES for item in files
    )

    return DiffSignals(
        files_changed=len(files),
        additions=sum(item.additions for item in files),
        deletions=sum(item.deletions for item in files),
        changed_lines=sum(item.additions + item.deletions for item in files),
        file_type_counts=dict(sorted(categories.items())),
        whitespace_only=whitespace_only,
        patch_coverage_complete=coverage_complete,
        contributor_files_only=contributor_only,
    )
