"""Extract a directly linked issue reference from PR text."""

import re

ISSUE_PATTERNS = (
    re.compile(r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE),
    re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+/issues/(\d+)", re.IGNORECASE),
)


def extract_issue_number(title: str, body: str) -> int | None:
    """Return the first issue number linked by closing syntax or a GitHub URL."""
    text = f"{title}\n{body}"
    for pattern in ISSUE_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None
