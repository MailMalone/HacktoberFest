import pytest

from pr_triage.webapp import _Handler, _is_loopback, _required_string


def test_web_server_only_accepts_loopback_hosts():
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0")
    assert not _is_loopback("example.com")


def test_required_string_strips_whitespace_and_rejects_empty_values():
    assert _required_string({"model": "  org/model  "}, "model", 100) == "org/model"
    with pytest.raises(ValueError, match="model is required"):
        _required_string({"model": "  "}, "model", 100)


def test_web_triage_requires_explicit_data_sharing_consent():
    with pytest.raises(ValueError, match="Confirm data sharing"):
        _Handler._triage(
            {
                "api_key": "key",
                "model": "org/model",
                "repo": "owner/repo",
                "pr_number": 1,
                "consent": False,
            }
        )
