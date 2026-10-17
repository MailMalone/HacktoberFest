from pathlib import Path
from types import SimpleNamespace

import pytest

from pr_triage.privacy import ConsentError, ensure_data_sharing_acknowledged


def test_acknowledgment_marker_skips_repeat_notice(monkeypatch, tmp_path: Path) -> None:
    marker = tmp_path / "privacy_ack"
    marker.write_text("acknowledged\n", encoding="utf-8")
    monkeypatch.setattr("pr_triage.privacy._acknowledgment_path", lambda: marker)

    ensure_data_sharing_acknowledged()


def test_noninteractive_run_requires_explicit_ack(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pr_triage.privacy._acknowledgment_path", lambda: tmp_path / "privacy_ack")
    monkeypatch.setattr(
        "pr_triage.privacy.sys.stdin",
        SimpleNamespace(isatty=lambda: False),
    )

    with pytest.raises(ConsentError):
        ensure_data_sharing_acknowledged()
