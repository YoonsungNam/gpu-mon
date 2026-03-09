"""
Unit tests for mock-dcgm-exporter metric generation.

Validates:
- All expected L1 and L2 DCGM metric names are present
- Correct number of series (NODE_COUNT × GPUS_PER_NODE)
- Required labels on every metric (node, gpu, gpu_model, env)
- Value ranges are physically plausible
"""

import os
import sys
import re
import pytest

# Add src root to path so we can import main directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as exporter

# ─── Constants ───────────────────────────────────────────────────────────────

L1_METRICS = [
    "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_FB_FREE",
    "DCGM_FI_DEV_GPU_TEMP",
    "DCGM_FI_DEV_POWER_USAGE",
    "DCGM_FI_DEV_SM_CLOCK",
    "DCGM_FI_DEV_XID_ERRORS",
]

L2_METRICS = [
    "DCGM_FI_PROF_SM_ACTIVE",
    "DCGM_FI_PROF_SM_OCCUPANCY",
    "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE",
    "DCGM_FI_PROF_DRAM_ACTIVE",
    "DCGM_FI_PROF_NVLINK_TX_BYTES",
]

REQUIRED_LABELS = ["node", "gpu", "gpu_model", "env"]

# Use small topology for deterministic tests
NODE_COUNT    = 2
GPUS_PER_NODE = 2


@pytest.fixture(scope="module")
def metrics_text():
    """Generate one snapshot of metrics with a 2-node × 2-GPU topology."""
    original_nc  = exporter.NODE_COUNT
    original_gpn = exporter.GPUS_PER_NODE
    exporter.NODE_COUNT    = NODE_COUNT
    exporter.GPUS_PER_NODE = GPUS_PER_NODE
    try:
        return exporter._build_metrics()
    finally:
        exporter.NODE_COUNT    = original_nc
        exporter.GPUS_PER_NODE = original_gpn


@pytest.fixture(scope="module")
def parsed_series(metrics_text):
    """
    Parse the Prometheus text format into:
        { metric_name: [(labels_dict, float_value), ...] }
    """
    result: dict[str, list[tuple[dict, float]]] = {}
    line_re = re.compile(r'^(\w+)\{([^}]+)\}\s+([\d.e+\-]+)$')

    for line in metrics_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = line_re.match(line)
        if not m:
            continue
        name, labels_str, value_str = m.group(1), m.group(2), m.group(3)
        labels = dict(
            kv.split("=", 1)
            for kv in labels_str.split(",")
        )
        labels = {k: v.strip('"') for k, v in labels.items()}
        result.setdefault(name, []).append((labels, float(value_str)))

    return result


# ─── L1 metric presence ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("metric_name", L1_METRICS)
def test_l1_metric_present(parsed_series, metric_name):
    assert metric_name in parsed_series, f"L1 metric missing: {metric_name}"


# ─── L2 metric presence ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("metric_name", L2_METRICS)
def test_l2_metric_present(parsed_series, metric_name):
    assert metric_name in parsed_series, f"L2 metric missing: {metric_name}"


# ─── Series count ─────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("metric_name", L1_METRICS + L2_METRICS)
def test_series_count(parsed_series, metric_name):
    expected = NODE_COUNT * GPUS_PER_NODE
    actual   = len(parsed_series[metric_name])
    assert actual == expected, (
        f"{metric_name}: expected {expected} series, got {actual}"
    )


# ─── Required labels ─────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("metric_name", L1_METRICS + L2_METRICS)
@pytest.mark.parametrize("label", REQUIRED_LABELS)
def test_required_label_present(parsed_series, metric_name, label):
    for labels, _ in parsed_series[metric_name]:
        assert label in labels, (
            f"{metric_name} missing label '{label}': {labels}"
        )


# ─── Label values ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_node_label_format(parsed_series):
    """node label should be 'mock-node-XX' for all series."""
    for labels, _ in parsed_series["DCGM_FI_DEV_GPU_UTIL"]:
        assert re.match(r"mock-node-\d{2}", labels["node"]), (
            f"Unexpected node label: {labels['node']}"
        )


@pytest.mark.unit
def test_gpu_label_is_numeric(parsed_series):
    for labels, _ in parsed_series["DCGM_FI_DEV_GPU_UTIL"]:
        assert labels["gpu"].isdigit(), f"gpu label not numeric: {labels['gpu']}"


@pytest.mark.unit
def test_env_label_is_homelab(parsed_series):
    for labels, _ in parsed_series["DCGM_FI_DEV_GPU_UTIL"]:
        assert labels["env"] == "homelab"


# ─── Value ranges ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_gpu_util_range(parsed_series):
    for _, v in parsed_series["DCGM_FI_DEV_GPU_UTIL"]:
        assert 0.0 <= v <= 100.0, f"GPU Util out of range: {v}"


@pytest.mark.unit
def test_gpu_temp_range(parsed_series):
    for _, v in parsed_series["DCGM_FI_DEV_GPU_TEMP"]:
        assert 0.0 <= v <= 120.0, f"GPU Temp out of range: {v}"


@pytest.mark.unit
def test_sm_active_range(parsed_series):
    for _, v in parsed_series["DCGM_FI_PROF_SM_ACTIVE"]:
        assert 0.0 <= v <= 1.0, f"SM Active out of range: {v}"


@pytest.mark.unit
def test_tensor_active_range(parsed_series):
    for _, v in parsed_series["DCGM_FI_PROF_PIPE_TENSOR_ACTIVE"]:
        assert 0.0 <= v <= 1.0, f"Tensor Active out of range: {v}"


@pytest.mark.unit
def test_dram_active_range(parsed_series):
    for _, v in parsed_series["DCGM_FI_PROF_DRAM_ACTIVE"]:
        assert 0.0 <= v <= 1.0, f"DRAM Active out of range: {v}"


@pytest.mark.unit
def test_xid_errors_non_negative(parsed_series):
    for _, v in parsed_series["DCGM_FI_DEV_XID_ERRORS"]:
        assert v >= 0.0, f"XID Errors negative: {v}"


@pytest.mark.unit
def test_fb_free_plus_used_equals_total(parsed_series):
    """FB_USED + FB_FREE should be approximately 40960 MiB (A100 40GB)."""
    used_series = {
        (l["node"], l["gpu"]): v
        for l, v in parsed_series["DCGM_FI_DEV_FB_USED"]
    }
    free_series = {
        (l["node"], l["gpu"]): v
        for l, v in parsed_series["DCGM_FI_DEV_FB_FREE"]
    }
    for key in used_series:
        total = used_series[key] + free_series[key]
        assert 39000 <= total <= 43000, (
            f"FB_USED + FB_FREE = {total} MiB, expected ~40960 for {key}"
        )


# ─── HELP and TYPE lines ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_help_lines_present(metrics_text):
    help_metrics = {
        line.split()[2]
        for line in metrics_text.splitlines()
        if line.startswith("# HELP")
    }
    for name in L1_METRICS + L2_METRICS:
        assert name in help_metrics, f"Missing # HELP line for {name}"


@pytest.mark.unit
def test_type_lines_present(metrics_text):
    type_metrics = {
        line.split()[2]
        for line in metrics_text.splitlines()
        if line.startswith("# TYPE")
    }
    for name in L1_METRICS + L2_METRICS:
        assert name in type_metrics, f"Missing # TYPE line for {name}"
