"""
Component tests — ClickHouse

Validates that ClickHouse is running with our schema applied:
  - HTTP interface responds
  - gpu_monitoring database exists
  - All schema tables exist with correct columns
  - INSERT and SELECT round-trip works per table
"""


import pytest
import requests

from tests.conftest import CLICKHOUSE_URL

pytestmark = pytest.mark.component

EXPECTED_TABLES = [
    "gpu_unified_logs",
    "s2_jobs",
    "s2_nodes",
    "s2_projects",
    "s2_pools",
    "vmware_vm_inventory",
]

# Column name → expected ClickHouse type substring
TABLE_COLUMNS = {
    "gpu_unified_logs": {
        "timestamp": "DateTime",
        "env": "String",
        "node_id": "String",
        "log_level": "String",
        "message": "String",
        "metadata": "String",
    },
    "s2_jobs": {
        "collected_at": "DateTime",
        "job_id": "String",
        "user_id": "String",
        "status": "String",
        "node_list": "Array",
        "gpu_indices": "Array",
    },
    "s2_nodes": {
        "node_id": "String",
        "status": "String",
        "gpu_total": "UInt",
        "gpu_allocated": "UInt",
    },
    "vmware_vm_inventory": {
        "vm_uuid": "String",
        "vm_status": "String",
        "gpu_count": "UInt",
        "gpu_type": "String",
    },
}


def _ch_query(sql: str) -> dict:
    r = requests.post(
        CLICKHOUSE_URL,
        params={"query": sql, "database": "gpu_monitoring"},
        timeout=10,
    )
    r.raise_for_status()
    return r


# ─── Basic connectivity ───────────────────────────────────────────────────────

def test_clickhouse_ping():
    r = requests.get(f"{CLICKHOUSE_URL}/ping", timeout=5)
    assert r.status_code == 200
    assert r.text.strip() == "Ok."


def test_clickhouse_select_one():
    r = requests.post(CLICKHOUSE_URL, params={"query": "SELECT 1"}, timeout=5)
    assert r.status_code == 200
    assert r.text.strip() == "1"


# ─── Database exists ─────────────────────────────────────────────────────────

def test_gpu_monitoring_database_exists():
    r = requests.post(
        CLICKHOUSE_URL,
        params={"query": "SHOW DATABASES"},
        timeout=5,
    )
    assert "gpu_monitoring" in r.text


# ─── All schema tables exist ─────────────────────────────────────────────────

@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_table_exists(table):
    r = requests.post(
        CLICKHOUSE_URL,
        params={
            "query": f"SELECT count() FROM system.tables WHERE database='gpu_monitoring' AND name='{table}'",
        },
        timeout=5,
    )
    assert r.text.strip() == "1", f"Table '{table}' not found in gpu_monitoring"


# ─── Column types match schema DDL ───────────────────────────────────────────

@pytest.mark.parametrize("table,columns", TABLE_COLUMNS.items())
def test_table_columns(table, columns):
    r = requests.post(
        CLICKHOUSE_URL,
        params={
            "query": (
                f"SELECT name, type FROM system.columns "
                f"WHERE database='gpu_monitoring' AND table='{table}' "
                f"FORMAT JSON"
            )
        },
        timeout=5,
    )
    data = r.json()["data"]
    actual = {row["name"]: row["type"] for row in data}

    for col, expected_type_substr in columns.items():
        assert col in actual, f"{table}.{col} column missing"
        assert expected_type_substr in actual[col], (
            f"{table}.{col}: expected type containing '{expected_type_substr}', "
            f"got '{actual[col]}'"
        )


# ─── INSERT / SELECT round-trip per table ────────────────────────────────────

def test_insert_and_select_gpu_unified_logs():
    requests.post(
        CLICKHOUSE_URL,
        params={
            "query": (
                "INSERT INTO gpu_monitoring.gpu_unified_logs "
                "(timestamp, env, cluster_id, node_id, log_level, source, message, metadata) "
                "VALUES (now(), 'test', 'test-cluster', 'test-node', 'INFO', 'system', 'test log', '{}')"
            )
        },
        timeout=5,
    ).raise_for_status()

    r = requests.post(
        CLICKHOUSE_URL,
        params={
            "query": (
                "SELECT count() FROM gpu_monitoring.gpu_unified_logs "
                "WHERE env='test' AND node_id='test-node'"
            )
        },
        timeout=5,
    )
    assert int(r.text.strip()) >= 1


def test_insert_and_select_s2_jobs():
    requests.post(
        CLICKHOUSE_URL,
        params={
            "query": (
                "INSERT INTO gpu_monitoring.s2_jobs "
                "(collected_at, job_id, job_name, user_id, team, queue, status, "
                " node_list, gpu_count, gpu_indices, cpu_count, memory_mb, metadata) "
                "VALUES (now(), 'test-job-1', 'test-job', 'test-user', 'test-team', "
                "        'default', 'running', ['node-1'], 2, [0, 1], 8, 16384, '{}')"
            )
        },
        timeout=5,
    ).raise_for_status()

    r = requests.post(
        CLICKHOUSE_URL,
        params={
            "query": "SELECT count() FROM gpu_monitoring.s2_jobs WHERE job_id='test-job-1'"
        },
        timeout=5,
    )
    assert int(r.text.strip()) >= 1


def test_insert_and_select_vmware_vm_inventory():
    requests.post(
        CLICKHOUSE_URL,
        params={
            "query": (
                "INSERT INTO gpu_monitoring.vmware_vm_inventory "
                "(collected_at, vm_name, vm_uuid, vm_status, esxi_host, cluster, "
                " resource_pool, guest_os, vcpu_count, memory_mb, gpu_count, "
                " gpu_type, gpu_profile, gpu_pci_ids, annotation, metadata) "
                "VALUES (now(), 'test-vm', 'test-uuid-001', 'poweredOn', "
                "        'esxi-test', 'cluster-a', 'pool-a', 'Ubuntu', "
                "        8, 32768, 1, 'passthrough', '', '[]', '', '{}')"
            )
        },
        timeout=5,
    ).raise_for_status()

    r = requests.post(
        CLICKHOUSE_URL,
        params={
            "query": "SELECT count() FROM gpu_monitoring.vmware_vm_inventory WHERE vm_uuid='test-uuid-001'"
        },
        timeout=5,
    )
    assert int(r.text.strip()) >= 1
