import threading

import httpx
import pytest

from pr_triage.maintenance import StateStore
from pr_triage.webapp import _Handler, _TriageServer


@pytest.fixture
def server(tmp_path):
    app = _TriageServer(("127.0.0.1", 0), _Handler)
    app.state_store = StateStore(tmp_path / "http.sqlite")
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{app.server_port}", trust_env=False) as client:
        yield client
    app.shutdown()
    app.server_close()
    thread.join(timeout=5)


def test_offline_demo_works_through_real_http_handler(server):
    response = server.post("/api/maintenance/demo", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["radar"]["scanned"] == 6
    assert data["claims"]["policy"]["enabled"] is False
    assert server.get("/app.js").status_code == 200


def test_cross_site_requests_cannot_enable_policy(server):
    response = server.post(
        "/api/claims/policy",
        json={"repo": "owner/repo"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_claim_writes_are_disabled_even_with_old_confirmation(server):
    payload = {"repo": "owner/repo", "policy": {"enabled": True}, "confirmed": True}
    for endpoint in ("/api/claims/policy", "/api/claims/run", "/api/claims/reset"):
        response = server.post(endpoint, json=payload)
        assert response.status_code == 400
        assert "read-only" in response.json()["error"]


def test_paused_policy_persists_and_invalid_days_are_rejected(server):
    response = server.post(
        "/api/claims/policy",
        json={
            "repo": "owner/repo",
            "policy": {"idle_days": 14, "grace_days": 3},
        },
    )
    assert response.status_code == 200
    assert response.json()["policy"]["enabled"] is False
    assert response.json()["policy"]["idle_days"] == 14
    response = server.post(
        "/api/claims/policy",
        json={
            "repo": "owner/repo",
            "policy": {"idle_days": 0},
        },
    )
    assert response.status_code == 400
