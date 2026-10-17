"""Read-only GitHub REST API client."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from typing_extensions import Self

from pr_triage.models import ChangedFile, PullRequest

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
MAX_FILE_PAGES = 30


class GitHubAPIError(RuntimeError):
    """Raised when GitHub data cannot be fetched or parsed."""


class GitHubClient:
    """Fetch public pull request evidence through GET requests only."""

    def __init__(self, token: str | None = None, client: httpx.AsyncClient | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.AsyncClient(
            base_url=API_BASE,
            headers=headers,
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
        )
        self._owns_client = client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        """Issue one GET request and convert HTTP failures to safe errors."""
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise GitHubAPIError(
                f"GitHub returned HTTP {exc.response.status_code} for {exc.request.url.path}."
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise GitHubAPIError(f"GitHub request failed: {type(exc).__name__}.") from exc

    @staticmethod
    def _repo_path(repo: str) -> str:
        """Encode the owner and repository path segments."""
        owner, name = repo.split("/", maxsplit=1)
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    async def get_pull_request(self, repo: str, number: int) -> PullRequest:
        """Fetch one pull request's public metadata."""
        data = await self._get(f"{self._repo_path(repo)}/pulls/{number}")
        try:
            return PullRequest(
                number=data["number"],
                title=data["title"],
                body=data.get("body") or "",
                author=data["user"]["login"],
                html_url=data.get("html_url", ""),
            )
        except (KeyError, TypeError) as exc:
            raise GitHubAPIError("GitHub pull request response was incomplete.") from exc

    async def list_open_pull_requests(self, repo: str, limit: int) -> list[PullRequest]:
        """Fetch up to 100 recent open pull requests."""
        data = await self._get(
            f"{self._repo_path(repo)}/pulls",
            params={"state": "open", "per_page": min(limit, 100), "page": 1},
        )
        try:
            return [
                PullRequest(
                    number=item["number"],
                    title=item["title"],
                    body=item.get("body") or "",
                    author=item["user"]["login"],
                    html_url=item.get("html_url", ""),
                )
                for item in data[:limit]
            ]
        except (KeyError, TypeError) as exc:
            raise GitHubAPIError("GitHub pull request list was incomplete.") from exc

    async def list_pull_request_files(self, repo: str, number: int) -> list[ChangedFile]:
        """Fetch changed-file metadata, following GitHub's Link pagination."""
        path = f"{self._repo_path(repo)}/pulls/{number}/files"
        params: dict[str, str | int] | None = {"per_page": 100, "page": 1}
        result: list[ChangedFile] = []

        for page in range(MAX_FILE_PAGES):
            try:
                response = await self._client.get(path, params=params)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as exc:
                raise GitHubAPIError(
                    f"GitHub returned HTTP {exc.response.status_code} while listing PR files."
                ) from exc
            except (httpx.RequestError, ValueError) as exc:
                raise GitHubAPIError(f"GitHub file request failed: {type(exc).__name__}.") from exc

            if not isinstance(data, list):
                raise GitHubAPIError("GitHub pull request files response was invalid.")
            try:
                result.extend(
                    ChangedFile(
                        filename=item["filename"],
                        status=item.get("status", "modified"),
                        additions=item.get("additions", 0),
                        deletions=item.get("deletions", 0),
                        patch=item.get("patch"),
                    )
                    for item in data
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise GitHubAPIError("GitHub changed-file metadata was invalid.") from exc

            next_link = response.links.get("next", {}).get("url")
            if not next_link:
                return result
            if page == MAX_FILE_PAGES - 1:
                raise GitHubAPIError(
                    "Pull request has more changed-file pages than the safety limit."
                )
            path = next_link
            params = None

        return result

    async def get_contributor_profile(self, username: str) -> dict[str, Any]:
        """Fetch public contributor account creation metadata."""
        safe_username = quote(username, safe="")
        data = await self._get(f"/users/{safe_username}")
        if not isinstance(data, dict) or "created_at" not in data:
            raise GitHubAPIError("GitHub contributor profile was incomplete.")
        return data

    async def count_repository_pull_requests(self, repo: str, username: str) -> int:
        """Count public PRs by a contributor in this repository."""
        data = await self._get(
            "/search/issues",
            params={
                "q": f"repo:{repo} is:pr author:{username}",
                "per_page": 1,
                "page": 1,
            },
        )
        try:
            return max(0, int(data["total_count"]) - 1)
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubAPIError("GitHub contributor search response was incomplete.") from exc

    async def get_linked_issue(self, repo: str, number: int) -> dict[str, Any]:
        """Fetch only issue state and labels; callers must omit issue bodies."""
        data = await self._get(f"{self._repo_path(repo)}/issues/{number}")
        if not isinstance(data, dict) or "state" not in data:
            raise GitHubAPIError("GitHub linked issue response was incomplete.")
        return data
