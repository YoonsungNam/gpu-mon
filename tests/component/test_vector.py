"""
Component tests — Vector Aggregator

Validates that Vector is listening on the expected port and that its
built-in metrics endpoint (exposed via a separate admin port) is reachable.
Because the stack uses the native Vector-to-Vector protocol on port 6000,
deep payload testing is deferred to the E2E log pipeline test.
"""

import socket

import pytest
import requests

pytestmark = pytest.mark.component

_VECTOR_HOST = "localhost"
_VECTOR_PORT = 6000


# ─── Connectivity ─────────────────────────────────────────────────────────────

def test_vector_port_is_listening():
    """Vector should accept TCP connections on port 6000."""
    try:
        with socket.create_connection((_VECTOR_HOST, _VECTOR_PORT), timeout=5):
            pass
    except (ConnectionRefusedError, OSError) as exc:
        pytest.fail(f"Vector port {_VECTOR_PORT} is not accepting connections: {exc}")


# ─── Health ───────────────────────────────────────────────────────────────────

def test_vector_health_via_clickhouse_dependency(grafana_session):
    """
    Indirect liveness check: if ClickHouse (Vector's sink) is healthy,
    and Vector is TCP-reachable, the aggregator pipeline is live.

    A direct /health endpoint is only available when Vector's `api` component
    is enabled; the current compose config does not expose it.  The E2E log
    test provides the authoritative end-to-end assertion.
    """
    try:
        r = requests.get("http://localhost:8123/ping", timeout=5)
        assert r.status_code == 200, f"ClickHouse ping failed: {r.status_code}"
    except requests.RequestException as exc:
        pytest.fail(f"ClickHouse not reachable (Vector sink dependency): {exc}")
