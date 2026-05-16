"""Smoke tests for backend.cli helpers exercised by the daemon lifecycle.

These cover the readiness-poll helper used by ``claudewatch start --daemon``.
The subprocess/signal-heavy paths in ``start``/``stop`` themselves are validated
manually with the CLI; see CLAUDE.md.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.cli import _wait_for_server_ready


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path == "/api/health":
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args, **kwargs):  # silence test output
        return


@pytest.fixture
def health_server():
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_wait_for_server_ready_returns_true_when_server_responds(health_server):
    url = health_server + "/api/health"
    start = time.time()
    assert _wait_for_server_ready(url, timeout_s=5.0, poll_interval_s=0.05) is True
    # Should respond well under the timeout.
    assert time.time() - start < 2.0


def test_wait_for_server_ready_returns_false_when_server_is_down():
    # Port 1 is virtually guaranteed to refuse connections under a normal user.
    url = "http://127.0.0.1:1/api/health"
    start = time.time()
    assert _wait_for_server_ready(url, timeout_s=0.5, poll_interval_s=0.1) is False
    # Should respect timeout and not hang.
    elapsed = time.time() - start
    assert 0.4 < elapsed < 3.0
