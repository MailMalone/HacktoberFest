from pr_triage.github_client import API_BASE, GitHubClient


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
