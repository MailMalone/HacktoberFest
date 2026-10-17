"""GitHub evidence collection and explicitly requested public comments."""

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


def _http_error_message(response: httpx.Response, path: str) -> str:
    """Translate GitHub's response into a useful, credential-safe error."""
    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("message", "") if isinstance(payload, dict) else ""
    detail = detail.strip() if isinstance(detail, str) else ""
    detail_lower = detail.lower()
    remaining = response.headers.get("X-RateLimit-Remaining")

    if status == 403 and (remaining == "0" or "api rate limit exceeded" in detail_lower):
        return (
            "GitHub's API rate limit was reached. Set GITHUB_TOKEN in .env for authenticated "
            "access, restart after changing it, and wait for the allowance to reset. "
            "Authenticated requests also have limits."
        )
    if status == 403 and "secondary rate limit" in detail_lower:
        return "GitHub's secondary rate limit was reached. Wait a few minutes, then retry."

    message = f"GitHub returned HTTP {status} for {path}."
    if detail:
        message = f"{message} GitHub says: {detail}"
    if status == 403:
        if "/issues/" in path and path.endswith("/comments"):
            message = (
                f"{message} This request posts a public issue/PR comment. For a fine-grained "
                "GITHUB_TOKEN, select this repository and grant Pull requests: Read and write "
                "(for PR comments) or Issues: Read and write (for issue comments). For the "
                "full claim workflow, grant both permissions, then restart the app."
            )
        else:
            message = (
                f"{message} Check GITHUB_TOKEN in .env; ensure it can access this repository "
                "and has the required permission for this operation."
            )
    return message


class GitHubClient:
    """Fetch PR evidence and post messages only when explicitly requested."""

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

    async def request_json(self, method: str, path: str, payload: dict) -> Any:
        """Perform a narrowly scoped maintenance mutation without retrying writes."""
        try:
            response = await self._client.request(method, path, json=payload)
            response.raise_for_status()
            return response.json() if response.content else None
        except httpx.HTTPStatusError as exc:
            raise GitHubAPIError(_http_error_message(exc.response, path)) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise GitHubAPIError(
                "GitHub action delivery is uncertain. Inspect GitHub before retrying."
            ) from exc

    async def paged(self, path: str, params: dict | None = None) -> list[dict]:
        """Read all pages or fail rather than act on incomplete activity evidence."""
        result = []
        for page in range(1, 11):
            data = await self._get(path, {**(params or {}), "per_page": 100, "page": page})
            if not isinstance(data, list):
                raise GitHubAPIError("Expected a GitHub activity list.")
            result.extend(data)
            if len(data) < 100:
                return result
        raise GitHubAPIError("Activity pagination limit reached; automatic actions withheld.")

    async def list_recent_pull_requests(self, repo: str, limit: int) -> list[dict]:
        """Return bounded metadata for radar comparisons, including historical PRs."""
        if not 1 <= limit <= 100:
            raise ValueError("Radar limit must be between 1 and 100.")
        data = await self._get(
            f"{self._repo_path(repo)}/pulls",
            {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": limit,
            },
        )
        return [
            {
                "number": item["number"],
                "title": item["title"],
                "url": item["html_url"],
                "author": item["user"]["login"],
                "state": "merged" if item.get("merged_at") else item["state"],
            }
            for item in data
        ]

    async def post_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        """Publish a maintainer-reviewed message to a verified pull request."""
        if not body.strip() or len(body) > 65536:
            raise ValueError("Comment must contain between 1 and 65,536 characters.")
        await self.get_pull_request(repo, number)
        path = f"{self._repo_path(repo)}/issues/{number}/comments"
        try:
            response = await self._client.post(path, json={"body": body.strip()})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not data.get("html_url"):
                raise GitHubAPIError(
                    "GitHub response was incomplete. Check the PR before retrying."
                )
            return {"html_url": data["html_url"]}
        except httpx.HTTPStatusError as exc:
            raise GitHubAPIError(_http_error_message(exc.response, path)) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise GitHubAPIError(
                "Could not confirm comment delivery. Check the PR before retrying."
            ) from exc

    async def rate_limit(self) -> dict[str, Any]:
        """Return the current core API allowance without exposing credentials."""
        data = await self._get("/rate_limit")
        return {key: data["resources"]["core"][key] for key in ("limit", "remaining", "reset")}

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
            raise GitHubAPIError(_http_error_message(exc.response, exc.request.url.path)) from exc
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
        """Fetch recent open pull requests across pages, up to the requested limit."""
        result: list[PullRequest] = []
        per_page = 100
        max_pages = 100
        path = f"{self._repo_path(repo)}/pulls"
        try:
            for page in range(1, max_pages + 1):
                data = await self._get(
                    path,
                    params={"state": "open", "per_page": per_page, "page": page},
                )
                if not isinstance(data, list):
                    raise TypeError("Pull request list must be an array.")
                result.extend(
                    PullRequest(
                        number=item["number"],
                        title=item["title"],
                        body=item.get("body") or "",
                        author=item["user"]["login"],
                        html_url=item.get("html_url", ""),
                    )
                    for item in data
                )
                if len(result) >= limit or len(data) < per_page:
                    return result[:limit]
        except (KeyError, TypeError) as exc:
            raise GitHubAPIError("GitHub pull request list was incomplete.") from exc
        raise GitHubAPIError(
            "Repository has more open pull requests than the pagination safety limit."
        )

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
                raise GitHubAPIError(_http_error_message(exc.response, path)) from exc
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
