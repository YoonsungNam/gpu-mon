"""
Shared pytest fixtures and helpers for gpu-mon tests.

Layer 3 (component) and Layer 4 (e2e) tests require the Docker Compose
stack to be running. Start it with:
    make dev-up

Tests marked with @pytest.mark.component or @pytest.mark.e2e are
automatically skipped when the stack is not detected.
"""

import time
import pytest
import requests


# ─── Base URLs ───────────────────────────────────────────────────────────────

MOCK_DCGM_URL     = "http://localhost:9400"
VMAGENT_URL       = "http://localhost:8429"
VMINSERT_URL      = "http://localhost:8480"
VMSELECT_URL      = "http://localhost:8481"
CLICKHOUSE_URL    = "http://localhost:8123"
VECTOR_URL        = "http://localhost:6000"
GRAFANA_URL       = "http://localhost:3000"
GRAFANA_USER      = "admin"
GRAFANA_PASS      = "admin"


# ─── Stack detection ─────────────────────────────────────────────────────────

def _stack_is_up() -> bool:
    """Return True if the Docker Compose stack appears to be running."""
    try:
        r = requests.get(f"{MOCK_DCGM_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def pytest_runtest_setup(item):
    """Auto-skip component/e2e tests when the stack is not running."""
    needs_stack = item.get_closest_marker("component") or item.get_closest_marker("e2e")
    if needs_stack and not _stack_is_up():
        pytest.skip("Docker Compose stack not running. Run 'make dev-up' first.")


# ─── poll_until helper ────────────────────────────────────────────────────────

def poll_until(fn, *, timeout: int = 60, interval: float = 2.0, label: str = "condition"):
    """
    Repeatedly call fn() until it returns a truthy value or timeout is reached.

    Args:
        fn:       Callable that returns truthy on success.
        timeout:  Maximum seconds to wait (default 60).
        interval: Seconds between attempts (default 2).
        label:    Human-readable description for the error message.

    Returns:
        The first truthy return value from fn().

    Raises:
        TimeoutError: If fn() never returns truthy within timeout.
    """
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None

    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except Exception as exc:
            last_exc = exc
        time.sleep(interval)

    msg = f"'{label}' not satisfied within {timeout}s"
    if last_exc:
        msg += f". Last error: {last_exc}"
    raise TimeoutError(msg)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def grafana_session():
    """Authenticated requests.Session for Grafana API."""
    s = requests.Session()
    s.auth = (GRAFANA_USER, GRAFANA_PASS)
    s.headers["Content-Type"] = "application/json"
    return s
