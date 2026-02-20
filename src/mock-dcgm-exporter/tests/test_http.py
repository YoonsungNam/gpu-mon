"""
Unit tests for mock-dcgm-exporter HTTP handler.

Starts the HTTP server in a background thread on an ephemeral port
and validates response codes, headers, and body format.
"""

import os
import sys
import time
import threading
import urllib.request
import urllib.error
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as exporter
from http.server import HTTPServer


@pytest.fixture(scope="module")
def test_server():
    """Start the HTTP server on a random port; yield (host, port); shut down."""
    server = HTTPServer(("127.0.0.1", 0), exporter.Handler)
    port = server.server_address[1]

    # Pre-populate metrics so /metrics doesn't return empty on first call
    exporter.NODE_COUNT    = 1
    exporter.GPUS_PER_NODE = 1
    with exporter._metrics_lock:
        exporter._metrics_text = exporter._build_metrics()

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield "127.0.0.1", port

    server.shutdown()


def _get(host, port, path):
    url = f"http://{host}:{port}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, dict(resp.headers), resp.read().decode()


def _get_status(host, port, path):
    try:
        return _get(host, port, path)[0]
    except urllib.error.HTTPError as e:
        return e.code


# ─── /metrics ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_metrics_returns_200(test_server):
    host, port = test_server
    status, _, _ = _get(host, port, "/metrics")
    assert status == 200


@pytest.mark.unit
def test_metrics_content_type(test_server):
    host, port = test_server
    _, headers, _ = _get(host, port, "/metrics")
    assert "text/plain" in headers.get("Content-Type", "")


@pytest.mark.unit
def test_metrics_body_not_empty(test_server):
    host, port = test_server
    _, _, body = _get(host, port, "/metrics")
    assert len(body) > 0


@pytest.mark.unit
def test_metrics_body_contains_dcgm_util(test_server):
    host, port = test_server
    _, _, body = _get(host, port, "/metrics")
    assert "DCGM_FI_DEV_GPU_UTIL" in body


@pytest.mark.unit
def test_metrics_body_contains_l2(test_server):
    host, port = test_server
    _, _, body = _get(host, port, "/metrics")
    assert "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE" in body


# ─── /health ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_health_returns_200(test_server):
    host, port = test_server
    status, _, body = _get(host, port, "/health")
    assert status == 200
    assert body == "ok"


@pytest.mark.unit
def test_healthz_returns_200(test_server):
    host, port = test_server
    status, _, body = _get(host, port, "/healthz")
    assert status == 200


# ─── unknown path ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_unknown_path_returns_404(test_server):
    host, port = test_server
    status = _get_status(host, port, "/notfound")
    assert status == 404
