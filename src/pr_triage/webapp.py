"""Loopback-only web interface for PR triage."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from pr_triage.classifier import ClassificationError, list_openrouter_models
from pr_triage.github_client import GitHubAPIError
from pr_triage.triage import triage_many

MAX_BODY_BYTES = 128_000
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
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
            if self.path == "/api/models":
                api_key = _required_string(payload, "api_key", 500)
                models = asyncio.run(list_openrouter_models(api_key))
                self._send_json(200, {"models": models})
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
        except (RuntimeError, OSError, TypeError) as exc:
            self._send_json(502, {"error": f"Request failed ({type(exc).__name__})."})

    @staticmethod
    def _triage(payload: dict[str, object]) -> list[dict[str, object]]:
        api_key = _required_string(payload, "api_key", 500)
        model = _required_string(payload, "model", 200)
        repo = _required_string(payload, "repo", 200)
        if not REPO_PATTERN.fullmatch(repo):
            raise ValueError("Repository must use owner/repo format.")
        try:
            number = int(payload.get("pr_number", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("PR number must be a positive integer.") from exc
        if number < 1:
            raise ValueError("PR number must be a positive integer.")
        if payload.get("consent") is not True:
            raise ValueError("Confirm data sharing before classification.")
        github_token = payload.get("github_token")
        if github_token is not None and not isinstance(github_token, str):
            raise ValueError("GitHub token must be text.")
        results = asyncio.run(
            triage_many(
                repo=repo,
                pr_numbers=[number],
                token=github_token or None,
                api_key=api_key,
                model=model,
                accept_data_sharing=True,
            )
        )
        return [result.model_dump(mode="json") for result in results]


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
    print(f"PR Triage web UI: http://{host}:{port}")
    print("API keys stay in the browser tab and request memory; press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPR Triage stopped.")
    finally:
        server.server_close()
