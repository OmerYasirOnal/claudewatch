"""Security regression tests for the FastAPI app.

Currently covers issue #39: DNS-rebinding defence via TrustedHostMiddleware.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a TestClient against the real `create_app`, with the scheduler
    loop neutered so lifespan startup is instant."""
    monkeypatch.setattr("backend.config.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("backend.server.STATE_DB", tmp_path / "state.db")

    async def _no_scheduler(s):
        return None

    monkeypatch.setattr("backend.server._scheduler_loop", _no_scheduler)

    from backend.server import create_app

    app = create_app()
    # Pin to 127.0.0.1 so the middleware allows by default. Individual tests
    # override the Host header to exercise the rejection path.
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_health_accepts_loopback_host(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_accepts_localhost_host(client):
    r = client.get("/api/health", headers={"Host": "localhost"})
    assert r.status_code == 200


def test_rebinding_host_is_rejected(client):
    """Issue #39: a request claiming Host: evil.example must be refused
    even though the underlying TCP connection went to 127.0.0.1.

    Starlette's TrustedHostMiddleware returns 400 in this case."""
    r = client.get("/api/health", headers={"Host": "evil.example"})
    assert r.status_code == 400


def test_rebinding_host_with_port_is_rejected(client):
    r = client.get("/api/health", headers={"Host": "attacker.local:7788"})
    assert r.status_code == 400


def test_rebinding_blocks_post_too(client):
    """POSTs are equally dangerous (CSRF-via-rebinding) — make sure the
    middleware applies before the route handler runs."""
    r = client.post(
        "/api/config",
        json={"port": 7799},
        headers={"Host": "evil.example"},
    )
    assert r.status_code == 400
