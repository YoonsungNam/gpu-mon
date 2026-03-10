"""
Unit tests for ClickHouseWriter.

clickhouse_driver.Client is mocked — no real ClickHouse required.
Tests cover: buffer accumulation, auto-flush on batch_size, flush(),
retry-on-failure (data re-buffered), and per-table isolation.
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Mock clickhouse_driver before import ─────────────────────────────────────
_ch_mock = types.ModuleType("clickhouse_driver")
_ch_mock.Client = MagicMock()
sys.modules.setdefault("clickhouse_driver", _ch_mock)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from writer.clickhouse_writer import ClickHouseWriter  # noqa: E402

# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute = MagicMock()
    return client


@pytest.fixture
def writer(mock_client):
    with patch("writer.clickhouse_writer.clickhouse_driver") as mock_module:
        mock_module.Client.return_value = mock_client
        w = ClickHouseWriter(
            endpoints=["clickhouse.test:9000"],
            database="gpu_monitoring",
            username="default",
            password="",
            batch_size=3,
            flush_interval="10s",
        )
        w._client = mock_client  # expose for assertion
        return w


# ─── Buffer accumulation ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_insert_below_batch_size_does_not_flush(writer):
    writer.insert("s2_jobs", [{"job_id": "1"}])
    writer.insert("s2_jobs", [{"job_id": "2"}])
    # batch_size=3, only 2 rows inserted
    writer._client.execute.assert_not_called()


@pytest.mark.unit
def test_insert_at_batch_size_triggers_flush(writer):
    rows = [{"job_id": str(i)} for i in range(3)]
    for row in rows:
        writer.insert("s2_jobs", [row])
    writer._client.execute.assert_called_once()


@pytest.mark.unit
def test_insert_multiple_batches(writer):
    rows = [{"job_id": str(i)} for i in range(6)]
    for row in rows:
        writer.insert("s2_jobs", [row])
    assert writer._client.execute.call_count == 2


# ─── explicit flush() ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_flush_sends_pending_rows(writer):
    writer.insert("s2_jobs", [{"job_id": "1"}])
    writer.flush()
    writer._client.execute.assert_called_once()
    # Buffer should be empty after flush
    assert writer._buffers.get("s2_jobs", []) == []


@pytest.mark.unit
def test_flush_with_empty_buffer_does_nothing(writer):
    writer.flush()
    writer._client.execute.assert_not_called()


# ─── Per-table isolation ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_separate_tables_have_separate_buffers(writer):
    writer.insert("s2_jobs", [{"job_id": "1"}])
    writer.insert("s2_nodes", [{"node_id": "n1"}])
    # Neither table has reached batch_size=3
    writer._client.execute.assert_not_called()

    writer.flush()
    # Both tables flushed — execute called twice
    assert writer._client.execute.call_count == 2


@pytest.mark.unit
def test_flush_one_table_does_not_affect_other(writer):
    writer.insert("s2_jobs", [{"job_id": "1"}])
    writer.insert("s2_nodes", [{"node_id": "n1"}])

    # Manually flush only s2_jobs
    with writer._lock:
        writer._flush_table("s2_jobs")

    assert writer._buffers.get("s2_jobs", []) == []
    assert len(writer._buffers.get("s2_nodes", [])) == 1


# ─── Retry on failure ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_flush_failure_rebuffers_rows(writer):
    """Rows that fail to INSERT are put back in the buffer to avoid data loss."""
    writer._client.execute.side_effect = Exception("connection lost")

    writer.insert("s2_jobs", [{"job_id": "x"}])
    writer.flush()

    # Row should be back in buffer
    assert len(writer._buffers.get("s2_jobs", [])) == 1


@pytest.mark.unit
def test_insert_table_name_in_execute_call(writer):
    rows = [{"job_id": str(i)} for i in range(3)]
    for row in rows:
        writer.insert("s2_jobs", [row])

    call_args = writer._client.execute.call_args[0]
    assert "s2_jobs" in call_args[0]
