"""
Component tests — VictoriaMetrics

Validates that the VictoriaMetrics cluster deployed via our Helm values
(environments/homelab/victoriametrics.yaml) is fully operational:
  - vminsert accepts writes
  - vmstorage persists data
  - vmselect can query written data
"""

import time

import pytest
import requests

from tests.conftest import VMINSERT_URL, VMSELECT_URL, poll_until

pytestmark = pytest.mark.component


# ─── vminsert health ─────────────────────────────────────────────────────────

def test_vminsert_health():
    r = requests.get(f"{VMINSERT_URL}/health", timeout=5)
    assert r.status_code == 200


def test_vminsert_ready():
    r = requests.get(f"{VMINSERT_URL}/-/ready", timeout=5)
    assert r.status_code == 200


# ─── vmselect health ─────────────────────────────────────────────────────────

def test_vmselect_health():
    r = requests.get(f"{VMSELECT_URL}/health", timeout=5)
    assert r.status_code == 200


def test_vmselect_ready():
    r = requests.get(f"{VMSELECT_URL}/-/ready", timeout=5)
    assert r.status_code == 200


# ─── Write → Read round-trip ─────────────────────────────────────────────────

def test_vminsert_accepts_prometheus_write():
    """POST a Prometheus remote_write payload and expect 204."""
    # Simple line protocol write via vminsert's import endpoint
    payload = "test_component_metric{test=\"victoriametrics\"} 42.0"
    r = requests.post(
        f"{VMINSERT_URL}/insert/0/prometheus/api/v1/import/prometheus",
        data=payload,
        headers={"Content-Type": "text/plain"},
        timeout=5,
    )
    assert r.status_code in (200, 204), f"vminsert rejected write: {r.status_code} {r.text}"


def test_vmselect_returns_written_metric():
    """Write a unique metric and confirm vmselect can query it."""
    unique_value = str(int(time.time()))
    metric_name = "test_vm_roundtrip"
    payload = f'{metric_name}{{marker="{unique_value}"}} 1.0'

    # Write
    requests.post(
        f"{VMINSERT_URL}/insert/0/prometheus/api/v1/import/prometheus",
        data=payload,
        headers={"Content-Type": "text/plain"},
        timeout=5,
    )

    # Poll until vmselect can see it
    def _query():
        r = requests.get(
            f"{VMSELECT_URL}/select/0/prometheus/api/v1/query",
            params={"query": f'{metric_name}{{marker="{unique_value}"}}'},
            timeout=5,
        )
        data = r.json()
        return data.get("data", {}).get("result", [])

    results = poll_until(_query, timeout=60, label="vmselect sees written metric")
    assert len(results) > 0


# ─── Prometheus API compatibility ────────────────────────────────────────────

def test_vmselect_instant_query_returns_valid_json():
    r = requests.get(
        f"{VMSELECT_URL}/select/0/prometheus/api/v1/query",
        params={"query": "1 + 1"},
        timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["data"]["resultType"] in ("scalar", "vector")


def test_vmselect_label_names_endpoint():
    r = requests.get(
        f"{VMSELECT_URL}/select/0/prometheus/api/v1/labels",
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "success"
