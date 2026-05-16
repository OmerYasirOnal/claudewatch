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

from backend.cli import _rotate_log_if_large, _wait_for_server_ready


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


# --- log rotation ----------------------------------------------------------


def test_rotate_log_shifts_files(tmp_path):
    """When the log exceeds max_bytes, rotate to .1; do it again → .1 + .2."""
    log_path = tmp_path / "server.log"
    log_path.write_bytes(b"x" * 100)

    _rotate_log_if_large(log_path, max_bytes=10, keep=5)
    assert not log_path.exists()
    assert (tmp_path / "server.log.1").exists()
    assert (tmp_path / "server.log.1").read_bytes() == b"x" * 100

    # Second rotation: write a fresh log, rotate again.
    log_path.write_bytes(b"y" * 100)
    _rotate_log_if_large(log_path, max_bytes=10, keep=5)
    assert not log_path.exists()
    assert (tmp_path / "server.log.1").exists()
    assert (tmp_path / "server.log.1").read_bytes() == b"y" * 100
    assert (tmp_path / "server.log.2").exists()
    assert (tmp_path / "server.log.2").read_bytes() == b"x" * 100


def test_rotate_log_drops_oldest(tmp_path):
    """If .1 .. .keep already exist, rotation drops .keep and shifts the rest."""
    log_path = tmp_path / "server.log"
    log_path.write_bytes(b"NEW" * 100)
    for i in range(1, 6):
        (tmp_path / f"server.log.{i}").write_bytes(f"old-{i}".encode())

    _rotate_log_if_large(log_path, max_bytes=10, keep=5)

    # The current log becomes .1.
    assert (tmp_path / "server.log.1").read_bytes() == b"NEW" * 100
    # The old .1 .. .4 shift to .2 .. .5.
    assert (tmp_path / "server.log.2").read_bytes() == b"old-1"
    assert (tmp_path / "server.log.3").read_bytes() == b"old-2"
    assert (tmp_path / "server.log.4").read_bytes() == b"old-3"
    assert (tmp_path / "server.log.5").read_bytes() == b"old-4"
    # The old .5 must be dropped — there is no .6.
    assert not (tmp_path / "server.log.6").exists()


def test_rotate_log_noop_when_small(tmp_path):
    """A file smaller than max_bytes is left untouched."""
    log_path = tmp_path / "server.log"
    log_path.write_bytes(b"tiny")

    _rotate_log_if_large(log_path, max_bytes=10 * 1024, keep=5)
    assert log_path.exists()
    assert log_path.read_bytes() == b"tiny"
    assert not (tmp_path / "server.log.1").exists()


def test_rotate_log_noop_when_missing(tmp_path):
    """A missing log file is also a no-op (no crash)."""
    log_path = tmp_path / "does-not-exist.log"
    _rotate_log_if_large(log_path, max_bytes=10, keep=5)
    assert not log_path.exists()
