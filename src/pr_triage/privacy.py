"""One-time local acknowledgment before PR text is sent to Gemini."""

import sys
from pathlib import Path


class ConsentError(RuntimeError):
    """Raised when data sharing has not been acknowledged."""


NOTICE = (
    "PR title, up to 2,000 characters of body, author, repository name, and computed "
    "signals will be sent to Google Gemini for classification. For private repositories, "
    "this sends repository metadata outside your machine. Tokens, raw diff patches, and "
    "issue bodies are excluded."
)


def _acknowledgment_path() -> Path:
    """Return the per-user acknowledgment marker location."""
    return Path.home() / ".pr-triage" / "privacy_ack"


def ensure_data_sharing_acknowledged(accept_data_sharing: bool = False) -> None:
    """Print a one-time notice and require explicit acknowledgment before model use."""
    marker = _acknowledgment_path()
    if marker.exists():
        return

    print(f"\nPrivacy notice: {NOTICE}", file=sys.stderr)
    if accept_data_sharing:
        accepted = True
    elif not sys.stdin.isatty():
        raise ConsentError(
            "No interactive input is available. Re-run with --accept-data-sharing after review."
        )
    else:
        print("Send this PR metadata to Gemini? [y/N] ", end="", file=sys.stderr)
        accepted = input().strip().lower() in {"y", "yes"}

    if not accepted:
        raise ConsentError("Classification stopped; PR metadata was not sent to Gemini.")

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("acknowledged\n", encoding="utf-8")
