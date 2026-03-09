"""
E2E test — Grafana datasource proxy queries

Verifies that Grafana can proxy live queries through both provisioned datasources:
  - VictoriaMetrics  →  PromQL instant query returns results
  - ClickHouse       →  SQL query returns results

This test runs after the metrics pipeline E2E (metrics must already be in VM).
"""

import pytest
import requests

from conftest import GRAFANA_URL, poll_until

pytestmark = pytest.mark.e2e


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_datasource_uid(session: requests.Session, name: str) -> str:
    r = session.get(f"{GRAFANA_URL}/api/datasources/name/{name}", timeout=5)
    r.raise_for_status()
    return r.json()["uid"]


# ─── VictoriaMetrics proxy ────────────────────────────────────────────────────

def test_grafana_proxies_victoriametrics_query(grafana_session):
    """
    Grafana should proxy a PromQL instant query through the VictoriaMetrics
    datasource and return at least one metric series.
    """
    uid = _get_datasource_uid(grafana_session, "VictoriaMetrics")

    def _query():
        r = grafana_session.get(
            f"{GRAFANA_URL}/api/datasources/proxy/uid/{uid}"
            f"/select/0/prometheus/api/v1/query",
            params={"query": "DCGM_FI_DEV_GPU_UTIL"},
            timeout=10,
        )
        if r.status_code != 200:
            return False
        body = r.json()
        return (
            body.get("status") == "success"
            and len(body["data"]["result"]) > 0
        )

    poll_until(
        _query,
        timeout=60,
        interval=5.0,
        label="DCGM_FI_DEV_GPU_UTIL via Grafana VictoriaMetrics proxy",
    )

    # Final assertion with full response for diagnostics
    uid = _get_datasource_uid(grafana_session, "VictoriaMetrics")
    r = grafana_session.get(
        f"{GRAFANA_URL}/api/datasources/proxy/uid/{uid}"
        f"/select/0/prometheus/api/v1/query",
        params={"query": "DCGM_FI_DEV_GPU_UTIL"},
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["result"], "PromQL query returned no results via Grafana proxy"


# ─── ClickHouse proxy ────────────────────────────────────────────────────────

def test_grafana_proxies_clickhouse_query(grafana_session):
    """
    Grafana should proxy a ClickHouse SQL query and return a valid response.
    We query system.tables as a lightweight sanity check (no data dependency).
    """
    uid = _get_datasource_uid(grafana_session, "ClickHouse")

    r = grafana_session.post(
        f"{GRAFANA_URL}/api/datasources/proxy/uid/{uid}/",
        data="SELECT count() FROM system.tables",
        timeout=10,
    )
    assert r.status_code == 200, (
        f"Grafana ClickHouse proxy query failed: {r.status_code} {r.text[:200]}"
    )
    # ClickHouse returns the count as plain text
    count = int(r.text.strip())
    assert count > 0, "system.tables is empty — unexpected"


# ─── Dashboard folder ─────────────────────────────────────────────────────────

def test_gpu_monitoring_folder_has_dashboards(grafana_session):
    """
    The 'GPU Monitoring' folder should contain at least one provisioned dashboard.
    """
    # Get folder UID
    r = grafana_session.get(f"{GRAFANA_URL}/api/folders", timeout=5)
    assert r.status_code == 200
    folders = {f["title"]: f["uid"] for f in r.json()}
    assert "GPU Monitoring" in folders, f"GPU Monitoring folder missing. Found: {list(folders)}"

    folder_uid = folders["GPU Monitoring"]
    r = grafana_session.get(
        f"{GRAFANA_URL}/api/search",
        params={"folderUIDs": folder_uid, "type": "dash-db"},
        timeout=5,
    )
    assert r.status_code == 200
    dashboards = r.json()
    assert len(dashboards) > 0, (
        f"GPU Monitoring folder '{folder_uid}' has no dashboards"
    )
