"""Loopback-only web interface for PR triage."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from dotenv import find_dotenv, load_dotenv

from pr_triage.classifier import ClassificationError, list_openrouter_models
from pr_triage.github_client import GitHubAPIError, GitHubClient
from pr_triage.maintenance import MONITOR_LOCK, ClaimPolicy, StateStore, inactive_issues
from pr_triage.maintenance_demo import build_demo
from pr_triage.radar import scan_radar
from pr_triage.triage import triage_many

MAX_BODY_BYTES = 128_000
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_SELECTED_PRS = 100
MAX_LISTED_PRS = 10_000
load_dotenv(find_dotenv(usecwd=True))


def _normalize_repo(value: str) -> str:
    """Accept owner/repo or a GitHub repository URL and return owner/repo."""
    value = value.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "github.com",
            "www.github.com",
        }:
            raise ValueError("Enter a github.com repository link.")
        value = parsed.path.strip("/")
    value = value.removesuffix(".git").strip("/")
    parts = value.split("/")
    repo = "/".join(parts[:2]) if len(parts) >= 2 else value
    if not REPO_PATTERN.fullmatch(repo):
        raise ValueError("Enter a repository as owner/repo or a GitHub repository link.")
    return repo


async def _get_open_pull_requests(
    repo: str, token: str | None, number: int | None
) -> list[dict[str, object]]:
    """Return one requested PR or recent open PRs for the picker."""
    async with GitHubClient(token=token) as client:
        pull_requests = (
            [await client.get_pull_request(repo, number)]
            if number is not None
            else await client.list_open_pull_requests(repo, MAX_LISTED_PRS)
        )
    return [
        {
            "number": pr.number,
            "title": pr.title,
            "author": pr.author,
            "html_url": pr.html_url,
        }
        for pr in pull_requests
    ]


def _is_loopback(host: str) -> bool:
    """Return whether a host string names a loopback address."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _TriageServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    server_version = "PR-Triage"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        """Keep request bodies and credentials out of logs."""
        super().log_message(format, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _local_origin_allowed(self) -> bool:
        """Reject cross-site browser posts and malformed Host headers."""
        host_value = self.headers.get("Host", "")
        try:
            host = urlsplit(f"//{host_value}").hostname or ""
            host_port = urlsplit(f"//{host_value}").port
        except ValueError:
            return False
        if not _is_loopback(host) or host_port != self.server.server_port:
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        try:
            parsed = urlsplit(origin)
            origin_port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and origin_port == self.server.server_port
            and _is_loopback(parsed.hostname or "")
        )

    def _read_json(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid content length."})
            return None
        if length < 1 or length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "Request body is empty or too large."})
            return None
        if "application/json" not in self.headers.get("Content-Type", ""):
            self._send_json(415, {"error": "Send a JSON request."})
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Invalid JSON."})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "JSON body must be an object."})
            return None
        return payload

    def do_GET(self) -> None:
        if not self._local_origin_allowed():
            self._send_json(403, {"error": "Use the local PR Triage address."})
            return
        if self.path == "/":
            body = files("pr_triage").joinpath("web/index.html").read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
        elif self.path == "/app.js":
            body = files("pr_triage").joinpath("web/app.js").read_bytes()
            self._send(200, body, "text/javascript; charset=utf-8")
        elif self.path == "/app.css":
            body = files("pr_triage").joinpath("web/app.css").read_bytes()
            self._send(200, body, "text/css; charset=utf-8")
        elif self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        if not self._local_origin_allowed():
            self._send_json(403, {"error": "Cross-origin request rejected."})
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            if self.path == "/api/maintenance/demo":
                self._send_json(200, asyncio.run(build_demo()))
                return
            if self.path in {
                "/api/radar",
                "/api/radar/exempt",
                "/api/claims",
                "/api/claims/policy",
                "/api/claims/run",
                "/api/claims/reset",
            }:
                self._send_json(200, self._maintenance(payload))
                return
            if self.path == "/api/models":
                api_key = os.getenv("OPENROUTER_API_KEY") or None
                models: list[dict[str, str]] = []
                warnings: list[str] = []
                if api_key:
                    try:
                        models.extend(asyncio.run(list_openrouter_models(api_key)))
                    except ClassificationError as exc:
                        warnings.append(str(exc))
                else:
                    warnings.append("Set OPENROUTER_API_KEY in .env to load supported models.")
                models.sort(key=lambda item: item["name"].casefold())
                self._send_json(
                    200,
                    {
                        "models": models,
                        "openrouter_configured": bool(api_key),
                        "warnings": warnings,
                    },
                )
                return
            if self.path == "/api/github/status":
                token = os.getenv("GITHUB_TOKEN") or None

                async def status():
                    async with GitHubClient(token=token) as client:
                        return await client.rate_limit()

                self._send_json(
                    200, {"configured": bool(token), "rate_limit": asyncio.run(status())}
                )
                return
            if self.path == "/api/comment":
                self._send_json(200, self._comment(payload))
                return
            if self.path == "/api/pulls":
                repo = _normalize_repo(_required_string(payload, "repo", 500))
                number_value = payload.get("pr_number")
                number = _positive_number(number_value) if number_value not in (None, "") else None
                pulls = asyncio.run(
                    _get_open_pull_requests(repo, os.getenv("GITHUB_TOKEN") or None, number)
                )
                self._send_json(200, {"repo": repo, "pull_requests": pulls})
                return
            if self.path == "/api/triage":
                result = self._triage(payload)
                self._send_json(200, {"results": result})
                return
            self._send_json(404, {"error": "Not found."})
        except (ValueError, ClassificationError) as exc:
            self._send_json(400, {"error": str(exc)})
        except GitHubAPIError as exc:
            self._send_json(502, {"error": str(exc)})
        except (RuntimeError, OSError, TypeError, KeyError) as exc:
            self._send_json(502, {"error": f"Request failed ({type(exc).__name__})."})

    def _maintenance(self, payload: dict[str, object]) -> dict:
        """Serve repository-scoped radar and opt-in claim management."""
        repo = _normalize_repo(_required_string(payload, "repo", 500)).lower()
        store = self.server.state_store
        token = os.getenv("GITHUB_TOKEN") or None
        with MONITOR_LOCK:
            if self.path in {"/api/claims/reset", "/api/claims/run"}:
                raise ValueError("Claim monitor is read-only. Scan inactive issues instead.")
            if self.path == "/api/radar/exempt":
                key = _required_string(payload, "key", 64)
                reason = _required_string(payload, "reason", 500)
                if not re.fullmatch(r"[a-f0-9]{64}", key):
                    raise ValueError("Invalid comparison key.")
                store.set(f"exception:{repo}:{key}", reason)
                return {"saved": True}
            policy = ClaimPolicy.model_validate(store.get(f"policy:{repo}", {}))
            if self.path == "/api/claims/policy":
                policy = ClaimPolicy.model_validate(payload.get("policy"))
                if policy.enabled:
                    raise ValueError("Claim monitor is read-only; automatic actions are disabled.")
                store.set(f"policy:{repo}", policy.model_dump())
                store.log(
                    repo,
                    "policy",
                    f"{'Enabled' if policy.enabled else 'Paused'}: {policy.idle_days} idle days, {policy.grace_days} grace days",
                )
                return {"policy": policy.model_dump()}

            async def run():
                async with GitHubClient(token=token) as client:
                    if self.path == "/api/radar":
                        limit = _positive_number(payload.get("limit", 30))
                        result = await scan_radar(client, repo, limit)
                        for match in result["matches"]:
                            match["exception"] = store.get(f"exception:{repo}:{match['key']}")
                        return result
                    if "idle_days" in payload:
                        policy_for_scan = policy.model_copy(
                            update={"idle_days": payload["idle_days"]}
                        )
                        policy_for_scan = ClaimPolicy.model_validate(policy_for_scan.model_dump())
                    else:
                        policy_for_scan = policy
                    return await inactive_issues(client, repo, policy_for_scan, store)

            return asyncio.run(run())

    @staticmethod
    def _comment(payload: dict[str, object]) -> dict[str, object]:
        """Post only an explicitly reviewed message using the server token."""
        token = os.getenv("GITHUB_TOKEN") or None
        if not token:
            raise ValueError("Set GITHUB_TOKEN in .env and restart the app to post comments.")
        if payload.get("confirmed") is not True:
            raise ValueError("Review and confirm the public message before posting.")
        repo = _normalize_repo(_required_string(payload, "repo", 500))
        number = _positive_number(payload.get("pr_number"))
        body = _required_string(payload, "body", 65536)

        async def publish():
            async with GitHubClient(token=token) as client:
                return await client.post_comment(repo, number, body)

        return asyncio.run(publish())

    @staticmethod
    def _triage(payload: dict[str, object]) -> list[dict[str, object]]:
        api_key = os.getenv("OPENROUTER_API_KEY") or None
        model = _required_string(payload, "model", 200)
        repo = _normalize_repo(_required_string(payload, "repo", 500))
        if payload.get("consent") is not True:
            raise ValueError("Confirm data sharing before classification.")
        provider = _required_string(payload, "provider", 20)
        if provider != "openrouter":
            raise ValueError("Choose a model from OpenRouter.")
        if not api_key:
            raise ValueError("Set OPENROUTER_API_KEY in .env to use OpenRouter models.")
        numbers_value = payload.get("pr_numbers")
        if not isinstance(numbers_value, list) or not numbers_value:
            raise ValueError("Select one or more pull requests first.")
        if len(numbers_value) > MAX_SELECTED_PRS:
            raise ValueError(f"Select no more than {MAX_SELECTED_PRS} pull requests at once.")
        numbers = list(dict.fromkeys(_positive_number(number) for number in numbers_value))
        results = asyncio.run(
            triage_many(
                repo=repo,
                pr_numbers=numbers,
                token=os.getenv("GITHUB_TOKEN") or None,
                api_key=api_key,
                model=model,
                accept_data_sharing=True,
            )
        )
        return [result.model_dump(mode="json") for result in results]


def _positive_number(value: object) -> int:
    """Validate a JSON PR number without accepting booleans or fractional values."""
    if isinstance(value, bool):
        raise ValueError("Pull request numbers must be positive integers.")  # noqa: TRY004
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Pull request numbers must be positive integers.") from exc
    if number < 1 or str(number) != str(value).strip():
        raise ValueError("Pull request numbers must be positive integers.")
    return number


def _required_string(payload: dict[str, object], field: str, max_length: int) -> str:
    """Validate a required, bounded string field."""
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{field.replace('_', ' ')} is required.")
    return value.strip()


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve the web interface on loopback only."""
    if not _is_loopback(host):
        raise ValueError("The web interface can bind only to a loopback address.")
    server = _TriageServer((host, port), _Handler)
    server.state_store = StateStore()
    print(f"PR Triage web UI: http://{host}:{port}")
    print("API keys stay on the server; press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPR Triage stopped.")
    finally:
        server.server_close()
