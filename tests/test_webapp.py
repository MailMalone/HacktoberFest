import pytest


def test_comment_requires_server_token_and_explicit_confirmation(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Set GITHUB_TOKEN"):
        _Handler._comment({"confirmed": True, "token": "browser-token"})
    monkeypatch.setenv("GITHUB_TOKEN", "server-token")
    with pytest.raises(ValueError, match="Review and confirm"):
        _Handler._comment({"confirmed": False})


def test_comment_uses_server_token_and_selected_destination(monkeypatch):
    from pr_triage import webapp

    class Client:
        def __init__(self, token):
            assert token == "server-token"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post_comment(self, repo, number, body):
            assert (repo, number, body) == ("owner/repo", 3, "Reviewed message")
            return {"html_url": "https://github.com/owner/repo/pull/3#issuecomment-1"}

    monkeypatch.setenv("GITHUB_TOKEN", "server-token")
    monkeypatch.setattr(webapp, "GitHubClient", Client)
    result = _Handler._comment(
        {"confirmed": True, "repo": "owner/repo", "pr_number": 3, "body": "Reviewed message"}
    )
    assert "html_url" in result


from pr_triage.webapp import (
    _Handler,
    _is_loopback,
    _normalize_repo,
    _positive_number,
    _required_string,
)


def test_web_server_only_accepts_loopback_hosts():
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0")
    assert not _is_loopback("example.com")


def test_required_string_strips_whitespace_and_rejects_empty_values():
    assert _required_string({"model": "  org/model  "}, "model", 100) == "org/model"
    with pytest.raises(ValueError, match="model is required"):
        _required_string({"model": "  "}, "model", 100)


def test_normalize_repo_accepts_repository_urls_and_owner_repo():
    assert _normalize_repo("https://github.com/ossu/computer-science") == "ossu/computer-science"
    assert (
        _normalize_repo("https://github.com/ossu/computer-science.git") == "ossu/computer-science"
    )
    assert _normalize_repo("ossu/computer-science") == "ossu/computer-science"
    with pytest.raises(ValueError, match="github.com"):
        _normalize_repo("https://example.com/ossu/computer-science")


def test_positive_number_rejects_bools_and_fractional_values():
    assert _positive_number(12) == 12
    assert _positive_number("12") == 12
    for value in (True, 0, 1.5, "01", "nope"):
        with pytest.raises(ValueError, match="positive integers"):
            _positive_number(value)


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


def test_web_triage_accepts_a_structured_output_model_from_the_picker(monkeypatch):
    from types import SimpleNamespace

    from pr_triage import webapp

    class Result:
        def model_dump(self, mode):
            return {"model": "vendor/model"}

    def fake_triage_many(**kwargs):
        assert kwargs["model"] == "vendor/model"

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(webapp, "triage_many", fake_triage_many)
    monkeypatch.setattr(webapp, "asyncio", SimpleNamespace(run=lambda coroutine: [Result()]))
    result = _Handler._triage(
        {
            "provider": "openrouter",
            "model": "vendor/model",
            "repo": "https://github.com/owner/repo",
            "pr_numbers": [1],
            "consent": True,
        }
    )

    assert result == [{"model": "vendor/model"}]


def test_web_triage_uses_server_environment_key_not_request_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Set OPENROUTER_API_KEY in .env"):
        _Handler._triage(
            {
                "api_key": "request-supplied-key",
                "provider": "openrouter",
                "model": "google/gemma-4-31b-it:free",
                "repo": "owner/repo",
                "pr_numbers": [1],
                "consent": True,
            }
        )
