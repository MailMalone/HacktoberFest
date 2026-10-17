import httpx
import pytest

from pr_triage.github_client import API_BASE, GitHubClient, _http_error_message


@pytest.mark.asyncio
async def test_comment_verifies_pr_and_posts_exact_reviewed_body_with_token(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.method == "GET":
            return httpx.Response(
                200, json={"number": 2, "title": "Test", "user": {"login": "dev"}}
            )
        assert request.url.path == "/repos/owner/repo/issues/2/comments"
        import json

        assert json.loads(request.content) == {"body": "Please add a regression test."}
        return httpx.Response(
            201, json={"html_url": "https://github.com/owner/repo/pull/2#issuecomment-1"}
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    async with GitHubClient(token="test-token") as client:
        result = await client.post_comment("owner/repo", 2, "Please add a regression test.")
    assert len(requests) == 2
    assert result["html_url"].endswith("issuecomment-1")


@pytest.mark.asyncio
async def test_comment_does_not_post_when_pr_cannot_be_verified():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(404, json={"message": "Not Found"})

    from pr_triage.github_client import GitHubAPIError

    async with httpx.AsyncClient(
        base_url=API_BASE, transport=httpx.MockTransport(handler)
    ) as transport:
        client = GitHubClient(client=transport)
        with pytest.raises(GitHubAPIError):
            await client.post_comment("owner/repo", 2, "Hello")


def test_github_rate_limit_error_explains_how_to_recover():
    response = httpx.Response(
        403,
        headers={"X-RateLimit-Remaining": "0"},
        json={"message": "API rate limit exceeded for 203.0.113.1."},
    )

    message = _http_error_message(response, "/repos/octo/demo/pulls/1290")

    assert "API rate limit" in message
    assert "GITHUB_TOKEN in .env" in message
    assert "203.0.113.1" not in message


def test_open_pull_request_listing_follows_pages_to_the_requested_limit():
    class FakeClient:
        def __init__(self):
            self.requests = []

        async def get(self, path, params=None):
            self.requests.append((path, params))
            start = 1 if params["page"] == 1 else 101
            count = 100 if params["page"] == 1 else 1
            return FakeResponse(
                [
                    {
                        "number": number,
                        "title": f"PR {number}",
                        "user": {"login": "contributor"},
                        "html_url": f"https://github.com/octo/demo/pull/{number}",
                    }
                    for number in range(start, start + count)
                ]
            )

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    client_transport = FakeClient()
    client = GitHubClient(client=client_transport)  # type: ignore[arg-type]
    operation = client.list_open_pull_requests("octo/demo", 150)
    try:
        operation.send(None)
    except StopIteration as completed:
        pulls = completed.value
    else:
        raise AssertionError("The mocked GitHub client unexpectedly suspended during pagination.")

    assert len(pulls) == 101
    assert pulls[-1].number == 101
    assert [request[1]["page"] for request in client_transport.requests] == [1, 2]


def test_pull_file_pagination_uses_get_requests_only() -> None:
    class FakeResponse:
        def __init__(self, data: list[dict], links: dict | None = None):
            self._data = data
            self.links = links or {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return self._data

    class FakeClient:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict | None]] = []

        async def get(self, path: str, params: dict | None = None) -> FakeResponse:
            self.requests.append((path, params))
            if params and params.get("page") == 1:
                return FakeResponse(
                    data=[
                        {
                            "filename": "src/a.py",
                            "status": "modified",
                            "additions": 1,
                            "deletions": 0,
                            "patch": "@@ -1 +1 @@\n-old\n+new",
                        }
                    ],
                    links={
                        "next": {
                            "url": f"{API_BASE}/repos/octo/demo/pulls/4/files?per_page=100&page=2"
                        }
                    },
                )
            return FakeResponse(
                data=[
                    {
                        "filename": "tests/test_a.py",
                        "status": "added",
                        "additions": 2,
                        "deletions": 0,
                        "patch": "@@ -0,0 +1,2 @@\n+assert True\n+assert False",
                    }
                ]
            )

    fake_client = FakeClient()
    client = GitHubClient(client=fake_client)  # type: ignore[arg-type]
    operation = client.list_pull_request_files("octo/demo", 4)
    try:
        operation.send(None)
    except StopIteration as completed:
        files = completed.value
    else:
        raise AssertionError("The fake HTTP client unexpectedly suspended during pagination.")
    requests = fake_client.requests

    assert len(files) == 2
    assert len(requests) == 2
    assert requests[0][0].endswith("/pulls/4/files")
    assert requests[1][0].endswith("page=2")
    assert requests[1][1] is None
