"""
Unit tests for S2 adapter: normalize functions + collect methods.

All HTTP calls are mocked — no real S2 API required.
"""

import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.s2_adapter import (
    S2Adapter,
    _normalize_job,
    _normalize_node,
    _normalize_pool,
    _normalize_project,
    _parse_time,
)

# ─── _parse_time ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_parse_time_iso_string():
    result = _parse_time("2025-07-15T09:00:00Z")
    assert isinstance(result, datetime)


@pytest.mark.unit
def test_parse_time_none_returns_none():
    assert _parse_time(None) is None


@pytest.mark.unit
def test_parse_time_empty_string_returns_none():
    assert _parse_time("") is None


@pytest.mark.unit
def test_parse_time_invalid_returns_none():
    assert _parse_time("not-a-date") is None


# ─── _normalize_job ──────────────────────────────────────────────────────────

@pytest.fixture
def raw_job():
    return {
        "id": 84723,
        "name": "llama-training",
        "user": "kim",
        "group": "ai-research",
        "partition": "high-priority",
        "state": "running",
        "submit_time": "2025-07-15T09:00:00Z",
        "start_time": "2025-07-15T09:05:00Z",
        "end_time": None,
        "nodes": ["gpu-node-03"],
        "gpu_count": 4,
        "gpu_indices": [0, 1, 2, 3],
        "cpu_count": 32,
        "memory_mb": 65536,
        "exit_code": None,
        "extra": {"priority": 100},
    }


@pytest.mark.unit
def test_normalize_job_fields(raw_job):
    row = _normalize_job(raw_job)
    assert row["job_id"] == "84723"
    assert row["job_name"] == "llama-training"
    assert row["user_id"] == "kim"
    assert row["team"] == "ai-research"
    assert row["queue"] == "high-priority"
    assert row["status"] == "running"
    assert row["node_list"] == ["gpu-node-03"]
    assert row["gpu_count"] == 4
    assert row["gpu_indices"] == [0, 1, 2, 3]
    assert row["cpu_count"] == 32
    assert row["memory_mb"] == 65536
    assert row["exit_code"] is None


@pytest.mark.unit
def test_normalize_job_collected_at_is_utc(raw_job):
    row = _normalize_job(raw_job)
    assert isinstance(row["collected_at"], datetime)


@pytest.mark.unit
def test_normalize_job_metadata_is_json(raw_job):
    row = _normalize_job(raw_job)
    parsed = json.loads(row["metadata"])
    assert parsed["priority"] == 100


@pytest.mark.unit
def test_normalize_job_missing_optional_fields():
    """Minimal raw job — only required fields — should not raise."""
    row = _normalize_job({"id": 1, "user": "u", "state": "pending"})
    assert row["job_id"] == "1"
    assert row["queue"] == "default"
    assert row["gpu_count"] == 0
    assert row["node_list"] == []


# ─── _normalize_node ─────────────────────────────────────────────────────────

@pytest.fixture
def raw_node():
    return {
        "name": "gpu-node-01",
        "state": "alloc",
        "partition": "high-priority",
        "gpu_total": 8,
        "gpu_allocated": 4,
        "cpu_total": 64,
        "cpu_allocated": 32,
    }


@pytest.mark.unit
def test_normalize_node_fields(raw_node):
    row = _normalize_node(raw_node)
    assert row["node_id"] == "gpu-node-01"
    assert row["status"] == "alloc"
    assert row["partition"] == "high-priority"
    assert row["gpu_total"] == 8
    assert row["gpu_allocated"] == 4
    assert row["cpu_total"] == 64
    assert row["cpu_allocated"] == 32


@pytest.mark.unit
def test_normalize_node_missing_fields():
    row = _normalize_node({"name": "node-x"})
    assert row["node_id"] == "node-x"
    assert row["gpu_total"] == 0


# ─── _normalize_project ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_normalize_project_fields():
    raw = {"id": 7, "name": "ai-research", "fairshare": 0.8, "gpu_limit": 500}
    row = _normalize_project(raw)
    assert row["project_id"] == "7"
    assert row["project_name"] == "ai-research"
    assert row["fairshare_weight"] == pytest.approx(0.8)
    assert row["gpu_limit"] == 500


@pytest.mark.unit
def test_normalize_project_defaults():
    row = _normalize_project({"id": 1, "name": "p"})
    assert row["fairshare_weight"] == pytest.approx(1.0)
    assert row["gpu_limit"] == 0


# ─── _normalize_pool ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_normalize_pool_fields():
    raw = {"id": 3, "name": "gpu-pool-a", "nodes": ["n1", "n2"], "gpu_total": 16}
    row = _normalize_pool(raw)
    assert row["pool_id"] == "3"
    assert row["pool_name"] == "gpu-pool-a"
    assert row["node_list"] == ["n1", "n2"]
    assert row["gpu_total"] == 16


# ─── S2Adapter.collect_* with mocked HTTP ────────────────────────────────────

@pytest.fixture
def mock_writer():
    w = MagicMock()
    w.insert = MagicMock()
    return w


@pytest.fixture
def adapter(mock_writer):
    with patch("adapters.s2_adapter.requests.Session") as mock_session_cls:
        session = MagicMock()
        mock_session_cls.return_value = session

        a = S2Adapter(
            api_url="http://s2-master.test:8080/api/v1",
            api_token="test-token",
            writer=mock_writer,
        )
        a._session_mock = session  # expose for per-test setup
        return a


@pytest.mark.unit
def test_collect_jobs_running_calls_writer(adapter, mock_writer):
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "jobs": [{"id": 1, "user": "u", "state": "running"}]
    }
    fake_response.raise_for_status = MagicMock()
    adapter.session.get.return_value = fake_response

    adapter.collect_jobs_running()

    mock_writer.insert.assert_called_once()
    table, rows = mock_writer.insert.call_args[0]
    assert table == "s2_jobs"
    assert len(rows) == 1


@pytest.mark.unit
def test_collect_nodes_calls_writer(adapter, mock_writer):
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "nodes": [{"name": "node-1", "state": "idle"}]
    }
    fake_response.raise_for_status = MagicMock()
    adapter.session.get.return_value = fake_response

    adapter.collect_nodes()

    mock_writer.insert.assert_called_once()
    table, _ = mock_writer.insert.call_args[0]
    assert table == "s2_nodes"


@pytest.mark.unit
def test_collect_jobs_empty_response_does_not_call_writer(adapter, mock_writer):
    fake_response = MagicMock()
    fake_response.json.return_value = {"jobs": []}
    fake_response.raise_for_status = MagicMock()
    adapter.session.get.return_value = fake_response

    adapter.collect_jobs_running()

    mock_writer.insert.assert_not_called()


@pytest.mark.unit
def test_collect_jobs_http_error_does_not_raise(adapter, mock_writer):
    """HTTP errors should be caught and logged, not propagated."""
    import requests as req_lib
    adapter.session.get.side_effect = req_lib.exceptions.ConnectionError("refused")

    # Must not raise
    adapter.collect_jobs_running()
    mock_writer.insert.assert_not_called()
