"""
Component tests — Grafana

Validates that Grafana is healthy and our provisioned datasources
(VictoriaMetrics, ClickHouse) are registered and reachable.
"""

import pytest
import requests

from conftest import GRAFANA_URL, VMSELECT_URL, CLICKHOUSE_URL

pytestmark = pytest.mark.component


# ─── Health ───────────────────────────────────────────────────────────────────

def test_grafana_health(grafana_session):
    r = grafana_session.get(f"{GRAFANA_URL}/api/health", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body.get("database") == "ok"


# ─── Datasources provisioned ─────────────────────────────────────────────────

def test_victoriametrics_datasource_exists(grafana_session):
    r = grafana_session.get(f"{GRAFANA_URL}/api/datasources", timeout=5)
    assert r.status_code == 200
    names = [ds["name"] for ds in r.json()]
    assert "VictoriaMetrics" in names, f"VictoriaMetrics datasource not found. Found: {names}"


def test_clickhouse_datasource_exists(grafana_session):
    r = grafana_session.get(f"{GRAFANA_URL}/api/datasources", timeout=5)
    assert r.status_code == 200
    names = [ds["name"] for ds in r.json()]
    assert "ClickHouse" in names, f"ClickHouse datasource not found. Found: {names}"


# ─── Datasource connectivity ─────────────────────────────────────────────────

def _get_datasource_id(session, name: str) -> int:
    r = session.get(f"{GRAFANA_URL}/api/datasources/name/{name}", timeout=5)
    r.raise_for_status()
    return r.json()["id"]


def test_victoriametrics_datasource_is_reachable(grafana_session):
    ds_id = _get_datasource_id(grafana_session, "VictoriaMetrics")
    r = grafana_session.get(
        f"{GRAFANA_URL}/api/datasources/{ds_id}/health",
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "OK", f"Datasource health failed: {body}"


def test_clickhouse_datasource_is_reachable(grafana_session):
    ds_id = _get_datasource_id(grafana_session, "ClickHouse")
    r = grafana_session.get(
        f"{GRAFANA_URL}/api/datasources/{ds_id}/health",
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "OK", f"Datasource health failed: {body}"


# ─── Dashboard provisioning ───────────────────────────────────────────────────

def test_grafana_dashboards_folder_exists(grafana_session):
    r = grafana_session.get(f"{GRAFANA_URL}/api/folders", timeout=5)
    assert r.status_code == 200
    titles = [f["title"] for f in r.json()]
    assert "GPU Monitoring" in titles, f"Dashboard folder not found. Found: {titles}"


# ─── Admin API accessible ────────────────────────────────────────────────────

def test_grafana_org_accessible(grafana_session):
    r = grafana_session.get(f"{GRAFANA_URL}/api/org", timeout=5)
    assert r.status_code == 200
    assert "id" in r.json()
