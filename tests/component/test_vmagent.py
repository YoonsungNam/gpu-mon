"""
Component tests — central vmagent

Validates that vmagent is healthy and that the mock-dcgm-exporter
scrape target is registered and actively scraped (health: up).
"""

import pytest
import requests

from tests.conftest import VMAGENT_URL, MOCK_DCGM_URL

pytestmark = pytest.mark.component


# ─── Health ───────────────────────────────────────────────────────────────────

def test_vmagent_health():
    r = requests.get(f"{VMAGENT_URL}/health", timeout=5)
    assert r.status_code == 200


def test_vmagent_metrics_endpoint():
    """vmagent should expose its own Prometheus metrics."""
    r = requests.get(f"{VMAGENT_URL}/metrics", timeout=5)
    assert r.status_code == 200
    assert "vmagent" in r.text.lower() or "vm_" in r.text


# ─── Targets ─────────────────────────────────────────────────────────────────

def _get_targets() -> dict:
    r = requests.get(f"{VMAGENT_URL}/api/v1/targets", timeout=10)
    r.raise_for_status()
    return r.json()


def test_targets_endpoint_is_reachable():
    data = _get_targets()
    assert "data" in data
    assert "activeTargets" in data["data"]


def test_mock_dcgm_target_is_registered():
    """mock-dcgm-exporter target must appear in vmagent's active targets."""
    data = _get_targets()
    targets = data["data"]["activeTargets"]
    job_names = [t.get("labels", {}).get("job", "") for t in targets]
    assert any("mock-dcgm" in j for j in job_names), (
        f"mock-dcgm target not found in active targets. Jobs: {job_names}"
    )


def test_mock_dcgm_target_health_is_up():
    """mock-dcgm-exporter should be successfully scraped (health: up)."""
    data = _get_targets()
    targets = data["data"]["activeTargets"]
    dcgm_targets = [
        t for t in targets
        if "mock-dcgm" in t.get("labels", {}).get("job", "")
    ]
    assert dcgm_targets, "No mock-dcgm targets found"
    for t in dcgm_targets:
        assert t.get("health") == "up", (
            f"mock-dcgm target not healthy: {t.get('lastError', 'no error info')}"
        )


# ─── Remote write ─────────────────────────────────────────────────────────────

def test_vmagent_remote_write_queue():
    """vmagent metrics should show a configured remote-write queue."""
    r = requests.get(f"{VMAGENT_URL}/metrics", timeout=5)
    assert r.status_code == 200
    # vmagent exposes vm_remotewrite_* metrics when remoteWrite is configured
    assert "vm_remotewrite" in r.text, (
        "Expected vm_remotewrite metrics — is remoteWrite configured?"
    )
