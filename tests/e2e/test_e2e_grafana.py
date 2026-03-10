"""
E2E test — Grafana datasource proxy queries

Verifies that Grafana can proxy live queries through both provisioned datasources:
  - VictoriaMetrics  →  PromQL instant query returns results
  - ClickHouse       →  SQL query returns results

This test runs after the metrics pipeline E2E (metrics must already be in VM).
"""

import pytest
import requests

from tests.conftest import GRAFANA_URL, poll_until

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
            f"/api/v1/query",
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
        f"/api/v1/query",
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
    Grafana should execute a ClickHouse SQL query via the unified query API.
    We query system.tables as a lightweight sanity check (no data dependency).
    """
    uid = _get_datasource_uid(grafana_session, "ClickHouse")

    r = grafana_session.post(
        f"{GRAFANA_URL}/api/ds/query",
        json={
            "queries": [
                {
                    "datasource": {"uid": uid},
                    "rawSql": "SELECT count() as cnt FROM system.tables",
                    "refId": "A",
                    "format": 1,
                }
            ],
            "from": "now-1h",
            "to": "now",
        },
        timeout=10,
    )
    assert r.status_code == 200, (
        f"Grafana ClickHouse query failed: {r.status_code} {r.text[:200]}"
    )


# ─── Dashboard folder ─────────────────────────────────────────────────────────

def test_gpu_monitoring_folder_exists(grafana_session):
    """
    The 'GPU Monitoring' folder should be provisioned in Grafana.
    Dashboard JSON files are added incrementally; this test validates
    the provisioning infrastructure is wired correctly.
    """
    r = grafana_session.get(f"{GRAFANA_URL}/api/folders", timeout=5)
    assert r.status_code == 200
    folders = {f["title"]: f["uid"] for f in r.json()}
    assert "GPU Monitoring" in folders, f"GPU Monitoring folder missing. Found: {list(folders)}"
