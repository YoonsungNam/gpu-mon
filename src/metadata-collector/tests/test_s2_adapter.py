"""
Unit tests for the S2 adapter (BMI REST + SSH 'phd' command design).

All network/SSH is mocked — no real BMI API, jump host, or login server
required.  ``subprocess.run`` and the BMI ``requests.Session`` are patched so
the tests are fully deterministic.

Coverage:
  - pure helpers: _int / _float / _ts_to_datetime / _safe_literal_eval
  - parsers: _parse_gpu_indices, _parse_phd_host, _parse_job_line, _parse_phd_list
  - normalizers: _normalize_node, _normalize_job
  - _BMIClient.get_grids / get_portal_spec (mocked Session)
  - _SSHPHDRunner.run (mocked subprocess.run)
  - S2Adapter.refresh_grids / collect_nodes / collect_jobs_active /
    collect_jobs_completed / _close_vanished_jobs
"""

import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.s2_adapter import (  # noqa: E402
    _LIST_FORMAT,
    S2Adapter,
    _BMIClient,
    _float,
    _int,
    _normalize_job,
    _normalize_node,
    _parse_gpu_indices,
    _parse_job_line,
    _parse_phd_host,
    _parse_phd_list,
    _safe_literal_eval,
    _SSHPHDRunner,
    _ts_to_datetime,
)

# A real-ish unix timestamp (2025-07-15 09:00:00 KST area).
_TS_SUBMIT = 1752537600.0
_TS_START = 1752537900.0
_TS_END = 1752541200.0


# ═══════════════════════════════════════════════════════════════════════
#  _int / _float
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_int_parses_valid():
    assert _int("42") == 42


@pytest.mark.unit
def test_int_fallback_on_garbage():
    assert _int("not-a-number") == 0
    assert _int(None) == 0
    assert _int("") == 0


@pytest.mark.unit
def test_float_parses_valid():
    assert _float("3.5") == pytest.approx(3.5)


@pytest.mark.unit
def test_float_fallback_on_garbage():
    assert _float("x") == pytest.approx(0.0)
    assert _float(None) == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════
#  _ts_to_datetime
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_ts_to_datetime_zero_is_none():
    assert _ts_to_datetime(0) is None


@pytest.mark.unit
def test_ts_to_datetime_negative_is_none():
    assert _ts_to_datetime(-5) is None


@pytest.mark.unit
def test_ts_to_datetime_real_timestamp():
    dt = _ts_to_datetime(_TS_SUBMIT)
    assert isinstance(dt, datetime)
    # adapter uses KST (UTC+9)
    assert dt.utcoffset().total_seconds() == pytest.approx(9 * 3600)


# ═══════════════════════════════════════════════════════════════════════
#  _safe_literal_eval
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_safe_literal_eval_list():
    assert _safe_literal_eval("['a', 'b']") == ["a", "b"]


@pytest.mark.unit
def test_safe_literal_eval_dict():
    assert _safe_literal_eval("{'k': 1}") == {"k": 1}


@pytest.mark.unit
def test_safe_literal_eval_invalid_returns_none():
    assert _safe_literal_eval("this is not python") is None
    assert _safe_literal_eval("{unterminated") is None


# ═══════════════════════════════════════════════════════════════════════
#  _parse_gpu_indices
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_parse_gpu_indices_empty():
    assert _parse_gpu_indices("") == []


@pytest.mark.unit
def test_parse_gpu_indices_single_host():
    result = _parse_gpu_indices("gpu-node-03[0,1,2,3]")
    assert result == [{"host": "gpu-node-03", "gpu_ids": [0, 1, 2, 3]}]


@pytest.mark.unit
def test_parse_gpu_indices_multi_host():
    result = _parse_gpu_indices("nodeA[0,1] | nodeB[2,3]")
    assert result == [
        {"host": "nodeA", "gpu_ids": [0, 1]},
        {"host": "nodeB", "gpu_ids": [2, 3]},
    ]


@pytest.mark.unit
def test_parse_gpu_indices_skips_malformed_segment():
    # 'garbage' has no [..] block → skipped; valid one still parsed
    result = _parse_gpu_indices("garbage | nodeB[5]")
    assert result == [{"host": "nodeB", "gpu_ids": [5]}]


# ═══════════════════════════════════════════════════════════════════════
#  _parse_phd_host
# ═══════════════════════════════════════════════════════════════════════

_PHD_HOST_OUTPUT = """\
host  op/link  jobs cores ucores memA memT swpA swpT l1 l5 l15 controller
------------------------------------------------------------------------
gpu-node-01 up/connected 2 64 32 500.5 512.0 8.0 16.0 1.5 1.2 0.9 ctrl-a
gpu-node-02 down/lost 0 64 0 510.0 512.0 16.0 16.0 0.1 0.2 0.3 ctrl-b
short line with too few tokens
"""


@pytest.mark.unit
def test_parse_phd_host_happy_path():
    nodes = _parse_phd_host(_PHD_HOST_OUTPUT)
    assert len(nodes) == 2  # the short line is skipped

    n0 = nodes[0]
    assert n0["hostname"] == "gpu-node-01"
    assert n0["op_status"] == "up"
    assert n0["link_status"] == "connected"
    assert n0["num_jobs"] == 2
    assert n0["cores_avail"] == 64
    assert n0["cores_used"] == 32
    assert n0["mem_avail_gb"] == pytest.approx(500.5)
    assert n0["mem_total_gb"] == pytest.approx(512.0)
    assert n0["swap_avail_gb"] == pytest.approx(8.0)
    assert n0["swap_total_gb"] == pytest.approx(16.0)
    assert n0["load_1m"] == pytest.approx(1.5)
    assert n0["load_5m"] == pytest.approx(1.2)
    assert n0["load_15m"] == pytest.approx(0.9)
    assert n0["controller"] == "ctrl-a"


@pytest.mark.unit
def test_parse_phd_host_op_without_link():
    raw = (
        "h o c\n"
        "----\n"
        "node-x running 1 64 0 1.0 2.0 3.0 4.0 0.1 0.2 0.3 ctrl\n"
    )
    nodes = _parse_phd_host(raw)
    assert len(nodes) == 1
    assert nodes[0]["op_status"] == "running"
    assert nodes[0]["link_status"] == ""  # no '/' in token


@pytest.mark.unit
def test_parse_phd_host_ignores_lines_before_separator():
    raw = "garbage header that should be ignored\nignored too\n"
    # no '---' separator → nothing is treated as data
    assert _parse_phd_host(raw) == []


# ═══════════════════════════════════════════════════════════════════════
#  _parse_job_line / _parse_phd_list
# ═══════════════════════════════════════════════════════════════════════

def _job_line(
    job_id=84723,
    user="kim",
    status="Running",
    project="ai-research",
    gpu_per_node=4,
    num_nodes=2,
    submit_ts=_TS_SUBMIT,
    start_ts=_TS_START,
    end_ts=_TS_END,
    resources="['gpu', 'highmem']",
    properties="{'_gpu_indices': 'nodeA[0,1,2,3] | nodeB[0,1,2,3]', 'prio': 100}",
):
    return (
        f"{job_id} {user} {status} {project} {gpu_per_node} {num_nodes} "
        f"{submit_ts} {start_ts} {end_ts} {resources} {properties}"
    )


@pytest.mark.unit
def test_parse_job_line_full():
    job = _parse_job_line(_job_line())
    assert job is not None
    assert job["job_id"] == 84723
    assert job["user"] == "kim"
    assert job["status"] == "Running"
    assert job["project"] == "ai-research"
    assert job["gpu_per_node"] == 4
    assert job["num_nodes"] == 2
    assert job["total_gpus"] == 8  # 4 * 2
    assert job["resources"] == ["gpu", "highmem"]
    assert job["submit_ts"] == pytest.approx(_TS_SUBMIT)
    assert job["end_ts"] == pytest.approx(_TS_END)
    assert isinstance(job["submit_time"], datetime)
    assert isinstance(job["start_time"], datetime)
    assert isinstance(job["end_time"], datetime)
    assert job["properties"]["prio"] == 100


@pytest.mark.unit
def test_parse_job_line_gpu_indices_in_properties():
    job = _parse_job_line(_job_line())
    assert job["gpu_indices_raw"] == "nodeA[0,1,2,3] | nodeB[0,1,2,3]"
    assert job["gpu_allocations"] == [
        {"host": "nodeA", "gpu_ids": [0, 1, 2, 3]},
        {"host": "nodeB", "gpu_ids": [0, 1, 2, 3]},
    ]


@pytest.mark.unit
def test_parse_job_line_no_bracket_returns_none():
    # no '[' → no resources array → rejected
    assert _parse_job_line("1 kim Running proj 1 1 0 0 0 no-array-here") is None


@pytest.mark.unit
def test_parse_job_line_too_few_fixed_fields_returns_none():
    # only 4 fixed fields before the '[' → < 9 → rejected
    assert _parse_job_line("1 kim Running proj ['gpu'] {}") is None


@pytest.mark.unit
def test_parse_job_line_unfinished_end_time_is_none():
    # end_ts == 0 → end_time should be None (running job)
    job = _parse_job_line(_job_line(status="Running", end_ts=0))
    assert job is not None
    assert job["end_time"] is None
    assert job["end_ts"] == pytest.approx(0.0)


@pytest.mark.unit
def test_parse_phd_list_multiple_lines_and_blank():
    raw = (
        _job_line(job_id=1)
        + "\n\n"  # blank line ignored
        + _job_line(job_id=2)
        + "\n"
    )
    jobs = _parse_phd_list(raw)
    assert [j["job_id"] for j in jobs] == [1, 2]


@pytest.mark.unit
def test_parse_phd_list_skips_unparseable_line():
    raw = _job_line(job_id=1) + "\nthis line has no bracket\n"
    jobs = _parse_phd_list(raw)
    assert [j["job_id"] for j in jobs] == [1]


# ═══════════════════════════════════════════════════════════════════════
#  _normalize_node
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def raw_node():
    return {
        "hostname": "gpu-node-01",
        "op_status": "up",
        "link_status": "connected",
        "num_jobs": 2,
        "cores_avail": 64,
        "cores_used": 32,
        "mem_avail_gb": 500.5,
        "mem_total_gb": 512.0,
        "swap_avail_gb": 8.0,
        "swap_total_gb": 16.0,
        "load_1m": 1.5,
        "load_5m": 1.2,
        "load_15m": 0.9,
        "controller": "ctrl-a",
    }


@pytest.mark.unit
def test_normalize_node_field_mapping(raw_node):
    row = _normalize_node("grid-east", raw_node)
    assert row["grid_name"] == "grid-east"
    assert row["hostname"] == "gpu-node-01"
    assert row["op_status"] == "up"
    assert row["link_status"] == "connected"
    assert row["num_jobs"] == 2
    assert row["cores_avail"] == 64
    assert row["cores_used"] == 32
    assert row["mem_avail_gb"] == pytest.approx(500.5)
    assert row["controller"] == "ctrl-a"
    assert isinstance(row["collected_at"], datetime)


@pytest.mark.unit
def test_normalize_node_raw_json_roundtrip(raw_node):
    row = _normalize_node("grid-east", raw_node)
    parsed = json.loads(row["raw_json"])
    assert parsed["hostname"] == "gpu-node-01"
    assert parsed["controller"] == "ctrl-a"


# ═══════════════════════════════════════════════════════════════════════
#  _normalize_job
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def raw_job():
    return _parse_job_line(_job_line())


@pytest.mark.unit
def test_normalize_job_field_mapping(raw_job):
    row = _normalize_job("grid-west", raw_job)
    assert row["grid_name"] == "grid-west"
    assert row["job_id"] == 84723
    assert row["user"] == "kim"
    assert row["status"] == "Running"
    assert row["project"] == "ai-research"
    assert row["gpu_per_node"] == 4
    assert row["num_nodes"] == 2
    assert row["total_gpus"] == 8
    assert row["submit_ts"] == pytest.approx(_TS_SUBMIT)
    assert row["resources"] == ["gpu", "highmem"]
    assert row["gpu_indices_raw"] == "nodeA[0,1,2,3] | nodeB[0,1,2,3]"
    assert isinstance(row["collected_at"], datetime)


@pytest.mark.unit
def test_normalize_job_json_columns(raw_job):
    row = _normalize_job("grid-west", raw_job)
    allocs = json.loads(row["gpu_allocations"])
    assert allocs[0]["host"] == "nodeA"
    props = json.loads(row["properties_json"])
    assert props["prio"] == 100
    # raw_json is serialisable even though it contains datetimes (default=str)
    assert isinstance(json.loads(row["raw_json"]), dict)


# ═══════════════════════════════════════════════════════════════════════
#  _BMIClient (mocked requests.Session)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_bmi_get_grids():
    with patch("adapters.s2_adapter.requests.Session") as mock_session_cls:
        session = MagicMock()
        mock_session_cls.return_value = session
        resp = MagicMock()
        resp.json.return_value = [{"name": "grid-a"}, {"name": "grid-b"}]
        session.get.return_value = resp

        client = _BMIClient("http://bmi.test/", {})
        grids = client.get_grids()

    assert grids == ["grid-a", "grid-b"]
    called_url = session.get.call_args[0][0]
    assert called_url == "http://bmi.test/api/v2/grid"


@pytest.mark.unit
def test_bmi_get_portal_spec():
    with patch("adapters.s2_adapter.requests.Session") as mock_session_cls:
        session = MagicMock()
        mock_session_cls.return_value = session
        resp = MagicMock()
        resp.json.return_value = [{"port": 6789, "machine": "login01.corp.example.com"}]
        session.get.return_value = resp

        client = _BMIClient("http://bmi.test", {})
        spec = client.get_portal_spec("grid-a")

    # machine domain stripped → "<port>@<short-host>"
    assert spec == "6789@login01"


@pytest.mark.unit
def test_bmi_get_portal_spec_empty():
    with patch("adapters.s2_adapter.requests.Session") as mock_session_cls:
        session = MagicMock()
        mock_session_cls.return_value = session
        resp = MagicMock()
        resp.json.return_value = []
        session.get.return_value = resp

        client = _BMIClient("http://bmi.test", {})
        assert client.get_portal_spec("grid-a") == ""


# ═══════════════════════════════════════════════════════════════════════
#  _SSHPHDRunner (mocked subprocess.run)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_ssh_phd_runner_returns_stdout():
    runner = _SSHPHDRunner(
        phd_bin_template="/opt/{grid_name}/bin/phd",
        jump_host="jump.test",
        login_host="login.test",
        ssh_user="svc",
    )
    completed = MagicMock(returncode=0, stdout="PHD OUTPUT", stderr="")
    with patch("adapters.s2_adapter.subprocess.run", return_value=completed) as mrun:
        out = runner.run(["host"], "grid-a")

    assert out == "PHD OUTPUT"
    ssh_cmd = mrun.call_args[0][0]
    assert ssh_cmd[0] == "ssh"
    # phd binary template formatted with the grid name, sent as the remote cmd
    assert "/opt/grid-a/bin/phd" in ssh_cmd[-1]
    # target host carries the ssh_user
    assert "svc@login.test" in ssh_cmd


@pytest.mark.unit
def test_ssh_phd_runner_nonzero_returns_empty():
    runner = _SSHPHDRunner(
        phd_bin_template="phd",
        jump_host="jump.test",
        login_host="login.test",
    )
    completed = MagicMock(returncode=1, stdout="partial", stderr="boom")
    with patch("adapters.s2_adapter.subprocess.run", return_value=completed):
        assert runner.run(["host"], "grid-a") == ""


@pytest.mark.unit
def test_ssh_phd_runner_timeout_returns_empty():
    import subprocess as _sp

    runner = _SSHPHDRunner(
        phd_bin_template="phd",
        jump_host="jump.test",
        login_host="login.test",
    )
    with patch(
        "adapters.s2_adapter.subprocess.run",
        side_effect=_sp.TimeoutExpired(cmd="ssh", timeout=120),
    ):
        assert runner.run(["host"], "grid-a") == ""


# ═══════════════════════════════════════════════════════════════════════
#  S2Adapter — fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_writer():
    w = MagicMock()
    w.insert = MagicMock()
    # writer._client.execute used for CH recovery / vanished detection
    w._client = MagicMock()
    w._client.execute = MagicMock(return_value=[])
    return w


@pytest.fixture
def adapter(mock_writer):
    """An S2Adapter whose BMI client and SSH runner are replaced by mocks."""
    with patch("adapters.s2_adapter.requests.Session"):
        a = S2Adapter(
            bmi_url="http://bmi.test",
            phd_bin_template="/opt/{grid_name}/bin/phd",
            http_proxy_addr="http://squid.test:3128",
            ssh_jump_host="jump.test",
            ssh_login_host="login.test",
            writer=mock_writer,
            ssh_user="svc",
            target_grids=["grid-a"],
        )
    a._bmi = MagicMock()
    a._phd = MagicMock()
    return a


# ═══════════════════════════════════════════════════════════════════════
#  S2Adapter.refresh_grids
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_refresh_grids_builds_map(adapter):
    adapter._bmi.get_grids.return_value = ["grid-a", "grid-b"]
    adapter._bmi.get_portal_spec.return_value = "6789@login01"

    adapter.refresh_grids()

    # only the target grid 'grid-a' is kept
    assert adapter._grid_map == {"grid-a": "6789@login01"}


@pytest.mark.unit
def test_refresh_grids_skips_target_not_in_bmi(adapter):
    # target grid-a is NOT returned by BMI → skipped, map stays empty
    adapter._bmi.get_grids.return_value = ["grid-z"]
    adapter._bmi.get_portal_spec.return_value = "1@x"

    adapter.refresh_grids()

    assert adapter._grid_map == {}
    adapter._bmi.get_portal_spec.assert_not_called()


@pytest.mark.unit
def test_refresh_grids_swallows_exception(adapter):
    adapter._bmi.get_grids.side_effect = RuntimeError("network down")
    # Must not raise
    adapter.refresh_grids()
    assert adapter._grid_map == {}


# ═══════════════════════════════════════════════════════════════════════
#  S2Adapter.collect_nodes
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_collect_nodes_inserts_rows(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}
    adapter._phd.run.return_value = _PHD_HOST_OUTPUT

    adapter.collect_nodes()

    adapter._phd.run.assert_called_once_with(["host"], "grid-a")
    mock_writer.insert.assert_called_once()
    table, rows = mock_writer.insert.call_args[0]
    assert table == "s2_nodes"
    assert len(rows) == 2
    assert rows[0]["grid_name"] == "grid-a"
    assert rows[0]["hostname"] == "gpu-node-01"


@pytest.mark.unit
def test_collect_nodes_no_rows_does_not_insert(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}
    adapter._phd.run.return_value = ""  # no phd output

    adapter.collect_nodes()

    mock_writer.insert.assert_not_called()


@pytest.mark.unit
def test_collect_nodes_calls_ensure_grids(mock_writer):
    """When the grid map is empty, collect_nodes triggers refresh_grids."""
    with patch("adapters.s2_adapter.requests.Session"):
        a = S2Adapter(
            bmi_url="http://bmi.test",
            phd_bin_template="/opt/{grid_name}/bin/phd",
            http_proxy_addr="http://squid.test:3128",
            ssh_jump_host="jump.test",
            ssh_login_host="login.test",
            writer=mock_writer,
            target_grids=["grid-a"],
        )
    a._bmi = MagicMock()
    a._bmi.get_grids.return_value = ["grid-a"]
    a._bmi.get_portal_spec.return_value = "1@login01"
    a._phd = MagicMock()
    a._phd.run.return_value = ""

    a.collect_nodes()

    # grid map got populated lazily via _ensure_grids → refresh_grids
    assert a._grid_map == {"grid-a": "1@login01"}


# ═══════════════════════════════════════════════════════════════════════
#  S2Adapter.collect_jobs_active
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_collect_jobs_active_inserts_running_and_queued(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}

    def fake_run(args, grid_name):
        # args == ["list", "-a", flag, "-O", _LIST_FORMAT]
        if "-r" in args:
            return _job_line(job_id=1, status="Running", end_ts=0)
        if "-q" in args:
            return _job_line(job_id=2, status="Queued", start_ts=0, end_ts=0)
        return ""

    adapter._phd.run.side_effect = fake_run

    adapter.collect_jobs_active()

    # one insert for Running, one for Queued
    insert_calls = [c for c in mock_writer.insert.call_args_list if c[0][0] == "s2_jobs"]
    assert len(insert_calls) == 2
    job_ids = {c[0][1][0]["job_id"] for c in insert_calls}
    assert job_ids == {1, 2}
    # the _LIST_FORMAT output spec was forwarded to phd via -O
    for call in adapter._phd.run.call_args_list:
        args = call[0][0]
        assert args[args.index("-O") + 1] == _LIST_FORMAT


@pytest.mark.unit
def test_collect_jobs_active_runs_vanished_detection_when_ok(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}
    adapter._phd.run.return_value = _job_line(job_id=1, status="Running", end_ts=0)
    # CH has no Running/Queued rows → nothing to close, but query IS executed
    mock_writer._client.execute.return_value = []

    adapter.collect_jobs_active()

    assert mock_writer._client.execute.called


@pytest.mark.unit
def test_collect_jobs_active_skips_vanished_detection_on_failure(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}
    # both phd calls raise → collection_ok=False → no vanished detection query
    adapter._phd.run.side_effect = RuntimeError("ssh broke")

    adapter.collect_jobs_active()

    mock_writer._client.execute.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
#  S2Adapter._close_vanished_jobs
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_close_vanished_jobs_marks_done(adapter, mock_writer):
    # CH knows job 999 (submit_ts 111.0) as Running; it's NOT in the active set
    ch_row = [
        999,                # job_id
        "kim",              # user
        "ai-research",      # project
        4,                  # gpu_per_node
        2,                  # num_nodes
        8,                  # total_gpus
        None,               # submit_time
        None,               # start_time
        111.0,              # submit_ts
        ["gpu"],            # resources
        "nodeA[0,1]",       # gpu_indices_raw
        "[]",               # gpu_allocations
        "{}",               # properties_json
    ]
    mock_writer._client.execute.return_value = [ch_row]

    # active set does NOT contain (999, 111.0) → job is vanished
    adapter._close_vanished_jobs("grid-a", active_job_keys={(1, 222.0)})

    mock_writer.insert.assert_called_once()
    table, rows = mock_writer.insert.call_args[0]
    assert table == "s2_jobs"
    assert len(rows) == 1
    closed = rows[0]
    assert closed["job_id"] == 999
    assert closed["status"] == "Done"
    assert closed["end_time"] is not None
    # closed marker present in raw_json
    assert json.loads(closed["raw_json"])["_closed_by"] == "vanished_detection"


@pytest.mark.unit
def test_close_vanished_jobs_keeps_still_active(adapter, mock_writer):
    ch_row = [
        999, "kim", "proj", 1, 1, 1, None, None, 111.0,
        [], "", "[]", "{}",
    ]
    mock_writer._client.execute.return_value = [ch_row]

    # job (999, 111.0) IS still active → not closed
    adapter._close_vanished_jobs("grid-a", active_job_keys={(999, 111.0)})

    mock_writer.insert.assert_not_called()


@pytest.mark.unit
def test_close_vanished_jobs_empty_ch_does_nothing(adapter, mock_writer):
    mock_writer._client.execute.return_value = []
    adapter._close_vanished_jobs("grid-a", active_job_keys=set())
    mock_writer.insert.assert_not_called()


@pytest.mark.unit
def test_close_vanished_jobs_ch_failure_is_swallowed(adapter, mock_writer):
    mock_writer._client.execute.side_effect = RuntimeError("CH down")
    # Must not raise, must not insert
    adapter._close_vanished_jobs("grid-a", active_job_keys=set())
    mock_writer.insert.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
#  S2Adapter.collect_jobs_completed
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_collect_jobs_completed_inserts_and_updates_last_end_ts(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}
    # in-memory last_end_ts already set → window = last - overlap, no CH recovery
    adapter._last_end_ts = {"grid-a": _TS_END}
    adapter._phd.run.return_value = _job_line(
        job_id=7, status="Done", end_ts=_TS_END + 5000,
    )

    adapter.collect_jobs_completed()

    insert_calls = [c for c in mock_writer.insert.call_args_list if c[0][0] == "s2_jobs"]
    assert len(insert_calls) == 1
    rows = insert_calls[0][0][1]
    assert rows[0]["job_id"] == 7
    assert rows[0]["status"] == "Done"
    # last_end_ts advanced to the max end_ts of the collected jobs
    assert adapter._last_end_ts["grid-a"] == pytest.approx(_TS_END + 5000)


@pytest.mark.unit
def test_collect_jobs_completed_sel_expr_uses_window(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}
    adapter._last_end_ts = {"grid-a": _TS_END}
    adapter._phd.run.return_value = ""

    adapter.collect_jobs_completed()

    args = adapter._phd.run.call_args[0][0]
    # ["list", "-a", "-sel", sel_expr, "-O", _LIST_FORMAT]
    assert "-sel" in args
    sel_expr = args[args.index("-sel") + 1]
    assert "status==Done" in sel_expr
    assert "end_time >" in sel_expr
    # window = last_end_ts - 600s overlap
    assert str(_TS_END - 600) in sel_expr


@pytest.mark.unit
def test_collect_jobs_completed_recovers_last_end_ts_from_ch(adapter, mock_writer):
    adapter._grid_map = {"grid-a": "6789@login01"}
    # no in-memory ts → _get_last_end_ts queries CH (returns submit_ts max)
    mock_writer._client.execute.return_value = [[_TS_END]]
    adapter._phd.run.return_value = ""

    adapter.collect_jobs_completed()

    # CH recovery happened
    assert mock_writer._client.execute.called
    # window derived from recovered ts (not the 7-day fallback)
    args = adapter._phd.run.call_args[0][0]
    sel_expr = args[args.index("-sel") + 1]
    assert str(_TS_END - 600) in sel_expr
