"""
E2E test — metrics pipeline

Verifies the full data path:
  mock-dcgm-exporter  →  vmagent (scrape)  →  vminsert  →  vmstorage
                      →  vmselect (query)

Uses poll_until (max 60 s) to account for scrape-interval lag (15 s by default).
"""

import pytest
import requests

from tests.conftest import (
    MOCK_DCGM_URL,
    VMSELECT_URL,
    poll_until,
)

pytestmark = pytest.mark.e2e

_METRIC = "DCGM_FI_DEV_GPU_UTIL"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _query_vmselect(metric: str) -> dict:
    """Run an instant PromQL query against vmselect."""
    r = requests.get(
        f"{VMSELECT_URL}/select/0/prometheus/api/v1/query",
        params={"query": metric},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _metric_has_results(metric: str) -> bool:
    try:
        body = _query_vmselect(metric)
        return (
            body.get("status") == "success"
            and len(body["data"]["result"]) > 0
        )
    except Exception:
        return False


# ─── Source reachability ──────────────────────────────────────────────────────

def test_mock_dcgm_is_up():
    """Sanity check: mock-dcgm-exporter is serving metrics."""
    r = requests.get(f"{MOCK_DCGM_URL}/metrics", timeout=5)
    assert r.status_code == 200
    assert _METRIC in r.text, f"{_METRIC} not found in mock-dcgm /metrics output"


# ─── End-to-end metric flow ───────────────────────────────────────────────────

def test_dcgm_metrics_appear_in_vmselect():
    """
    After vmagent scrapes mock-dcgm, DCGM_FI_DEV_GPU_UTIL should be queryable
    via vmselect within 60 seconds (accounts for 15s scrape interval + write lag).
    """
    poll_until(
        lambda: _metric_has_results(_METRIC),
        timeout=60,
        interval=5.0,
        label=f"{_METRIC} in vmselect",
    )
    # Perform final assertion with detailed error on failure
    body = _query_vmselect(_METRIC)
    assert body["status"] == "success"
    results = body["data"]["result"]
    assert results, f"No series returned for {_METRIC}"


def test_required_labels_on_scraped_metric():
    """
    Scraped DCGM metrics must carry 'node', 'gpu', 'env', and 'job' labels.
    """
    poll_until(
        lambda: _metric_has_results(_METRIC),
        timeout=60,
        interval=5.0,
        label=f"{_METRIC} labels check",
    )
    body = _query_vmselect(_METRIC)
    for series in body["data"]["result"]:
        labels = series["metric"]
        for required in ("node", "gpu", "env", "job"):
            assert required in labels, (
                f"Required label '{required}' missing from series: {labels}"
            )


def test_multiple_gpu_series_scraped():
    """
    The compose stack uses NODE_COUNT=4 × GPUS_PER_NODE=2 = 8 GPU series.
    At least 2 series must be present (conservative lower bound for CI).
    """
    poll_until(
        lambda: _metric_has_results(_METRIC),
        timeout=60,
        interval=5.0,
        label="multiple GPU series in vmselect",
    )
    body = _query_vmselect(_METRIC)
    series_count = len(body["data"]["result"])
    assert series_count >= 2, (
        f"Expected at least 2 GPU series, got {series_count}"
    )


def test_l2_metric_also_propagates():
    """L2 profiling metrics should also be scraped end-to-end."""
    l2_metric = "DCGM_FI_PROF_SM_ACTIVE"
    poll_until(
        lambda: _metric_has_results(l2_metric),
        timeout=60,
        interval=5.0,
        label=f"{l2_metric} in vmselect",
    )
    body = _query_vmselect(l2_metric)
    assert body["status"] == "success"
    assert body["data"]["result"], f"No series for {l2_metric}"
