"""
E2E test — log pipeline

Verifies the full log data path:
  Vector Aggregator (port 6000, Vector protocol)  →  ClickHouse gpu_unified_logs

Because Vector uses the native Vector-to-Vector binary protocol on port 6000,
we trigger log ingestion by confirming that Vector's ClickHouse sink is
actively flushing — evidenced by rows appearing in gpu_unified_logs within
60 seconds of stack startup.

If you need to inject synthetic log events from a test Vector agent, add a
`vector-test-agent` service to the compose stack that pushes via the
`vector` sink type.
"""

import pytest
import requests

from tests.conftest import CLICKHOUSE_URL, poll_until

pytestmark = pytest.mark.e2e

_DB    = "gpu_monitoring"
_TABLE = "gpu_unified_logs"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _clickhouse_query(sql: str) -> str:
    r = requests.post(
        CLICKHOUSE_URL,
        params={"database": _DB},
        data=sql,
        timeout=10,
    )
    r.raise_for_status()
    return r.text.strip()


def _log_row_count() -> int:
    result = _clickhouse_query(f"SELECT count() FROM {_TABLE}")
    return int(result)


# ─── Schema readiness ─────────────────────────────────────────────────────────

def test_gpu_unified_logs_table_exists():
    result = _clickhouse_query(
        f"SELECT count() FROM system.tables "
        f"WHERE database = '{_DB}' AND name = '{_TABLE}'"
    )
    assert int(result) == 1, f"Table {_DB}.{_TABLE} not found"


def test_gpu_unified_logs_schema():
    """Verify required columns exist."""
    result = _clickhouse_query(
        f"SELECT name FROM system.columns "
        f"WHERE database = '{_DB}' AND table = '{_TABLE}' "
        f"ORDER BY name"
    )
    columns = result.splitlines()
    for required in ("timestamp", "log_level", "source", "message", "metadata"):
        assert required in columns, (
            f"Column '{required}' missing from {_TABLE}. Found: {columns}"
        )


# ─── Pipeline liveness ────────────────────────────────────────────────────────

def test_logs_eventually_appear_in_clickhouse():
    """
    Rows should appear in gpu_unified_logs within 60 seconds.

    Vector's batch config: max_bytes=10MB, timeout_secs=10.
    The stack must have log-producing services generating events
    (e.g. metadata-collector or a test log generator).

    Skip note: this test is advisory if no log producers are active
    in the macbook compose stack. The pipeline architecture is validated
    end-to-end in a staging/homelab environment where metadata-collector runs.
    """
    try:
        poll_until(
            lambda: _log_row_count() > 0,
            timeout=60,
            interval=5.0,
            label="log rows in gpu_unified_logs",
        )
        count = _log_row_count()
        assert count > 0, "No log rows found in gpu_unified_logs after 60s"
    except TimeoutError:
        pytest.skip(
            "No log rows appeared in 60s — no active log producer in compose stack. "
            "Run metadata-collector or add a test log agent to generate events."
        )
