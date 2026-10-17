"""Identify test and CI changes using repository paths only."""

from pathlib import PurePosixPath

from pr_triage.models import ChangedFile, TestSignals

TEST_DIR_NAMES = {"test", "tests", "spec", "specs"}
CI_PATH_MARKERS = {".github/workflows", ".circleci", ".buildkite"}
CI_FILES = {".travis.yml", "azure-pipelines.yml", "jenkinsfile"}


def _is_test_file(filename: str) -> bool:
    path = PurePosixPath(filename.lower())
    name = path.name
    return (
        any(part in TEST_DIR_NAMES for part in path.parts)
        or name.startswith(("test_", "spec_"))
        or name.endswith(("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js"))
    )


def _is_ci_file(filename: str) -> bool:
    normalized = filename.lower().replace("\\", "/")
    normalized = normalized.removeprefix("./")
    return any(normalized.startswith(marker + "/") for marker in CI_PATH_MARKERS) or (
        PurePosixPath(normalized).name in CI_FILES
    )


def detect_tests(files: list[ChangedFile]) -> TestSignals:
    """Count test files and detect changes to CI configuration."""
    test_count = sum(_is_test_file(item.filename) for item in files)
    return TestSignals(
        tests_changed=test_count > 0,
        test_files_changed=test_count,
        ci_changed=any(_is_ci_file(item.filename) for item in files),
    )
