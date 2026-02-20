"""
Mock DCGM Exporter

Generates synthetic GPU metrics (L1 + L2 DCGM counters) for development
and homelab environments without real NVIDIA GPUs.

Exposes a /metrics endpoint compatible with vmagent scraping.

Environment variables:
  NODE_COUNT      — Number of simulated GPU nodes (default: 2)
  GPUS_PER_NODE   — GPUs per node (default: 2)
  GPU_MODEL       — GPU model name label (default: A100)
  PORT            — HTTP port to listen on (default: 9400)
  SCRAPE_INTERVAL — Seconds between metric updates (default: 15)
"""

import os
import math
import random
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── Config ──────────────────────────────────────────────────────────────────

NODE_COUNT      = int(os.environ.get("NODE_COUNT", 2))
GPUS_PER_NODE   = int(os.environ.get("GPUS_PER_NODE", 2))
GPU_MODEL       = os.environ.get("GPU_MODEL", "A100")
PORT            = int(os.environ.get("PORT", 9400))
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", 15))

# ─── Metric state ────────────────────────────────────────────────────────────

_metrics_text = ""
_metrics_lock = threading.Lock()


def _gpu_label(node_idx: int, gpu_idx: int) -> str:
    node_name = f"mock-node-{node_idx:02d}"
    return f'node="{node_name}",gpu="{gpu_idx}",gpu_model="{GPU_MODEL}",env="homelab"'


def _simulate_util(t: float, node: int, gpu: int) -> float:
    """Produces a slowly varying GPU utilization with some per-GPU variance."""
    base = 60 + 20 * math.sin(t / 120 + node * 1.3 + gpu * 0.7)
    noise = random.gauss(0, 3)
    return max(0.0, min(100.0, base + noise))


def _build_metrics() -> str:
    t = time.time()
    lines = []

    def metric(name: str, help_text: str, mtype: str = "gauge"):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")

    # ── L1: Hardware counters ─────────────────────────────────────────────
    metric("DCGM_FI_DEV_GPU_UTIL", "GPU utilization (in %).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            v = _simulate_util(t, n, g)
            lines.append(f'DCGM_FI_DEV_GPU_UTIL{{{_gpu_label(n, g)}}} {v:.1f}')

    metric("DCGM_FI_DEV_FB_USED", "Frame buffer memory used (in MiB).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            mem = 4096 + (util / 100) * 36000 + random.gauss(0, 200)
            lines.append(f'DCGM_FI_DEV_FB_USED{{{_gpu_label(n, g)}}} {mem:.0f}')

    metric("DCGM_FI_DEV_FB_FREE", "Frame buffer memory free (in MiB).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            total = 40960  # 40 GiB A100
            util = _simulate_util(t, n, g)
            used = 4096 + (util / 100) * 36000
            lines.append(f'DCGM_FI_DEV_FB_FREE{{{_gpu_label(n, g)}}} {total - used:.0f}')

    metric("DCGM_FI_DEV_GPU_TEMP", "GPU temperature (in C).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            temp = 40 + (util / 100) * 40 + random.gauss(0, 1)
            lines.append(f'DCGM_FI_DEV_GPU_TEMP{{{_gpu_label(n, g)}}} {temp:.1f}')

    metric("DCGM_FI_DEV_POWER_USAGE", "Power draw (in W).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            power = 100 + (util / 100) * 300 + random.gauss(0, 5)
            lines.append(f'DCGM_FI_DEV_POWER_USAGE{{{_gpu_label(n, g)}}} {power:.1f}')

    metric("DCGM_FI_DEV_SM_CLOCK", "SM clock frequency (in MHz).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            clock = 1000 + (util / 100) * 755 + random.gauss(0, 10)
            lines.append(f'DCGM_FI_DEV_SM_CLOCK{{{_gpu_label(n, g)}}} {clock:.0f}')

    metric("DCGM_FI_DEV_XID_ERRORS", "Value of the last XID error encountered.", "counter")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            lines.append(f'DCGM_FI_DEV_XID_ERRORS{{{_gpu_label(n, g)}}} 0')

    # ── L2: Profiling counters ────────────────────────────────────────────
    metric("DCGM_FI_PROF_SM_ACTIVE", "Ratio of cycles an SM has at least 1 warp assigned (0.0-1.0).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            v = (util / 100) * 0.85 + random.gauss(0, 0.02)
            lines.append(f'DCGM_FI_PROF_SM_ACTIVE{{{_gpu_label(n, g)}}} {max(0.0, min(1.0, v)):.3f}')

    metric("DCGM_FI_PROF_SM_OCCUPANCY", "Ratio of warps resident to theoretical max (0.0-1.0).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            v = (util / 100) * 0.65 + random.gauss(0, 0.03)
            lines.append(f'DCGM_FI_PROF_SM_OCCUPANCY{{{_gpu_label(n, g)}}} {max(0.0, min(1.0, v)):.3f}')

    metric("DCGM_FI_PROF_PIPE_TENSOR_ACTIVE", "Ratio of cycles the tensor (HMMA) pipe is active (0.0-1.0).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            v = (util / 100) * 0.55 + random.gauss(0, 0.04)
            lines.append(f'DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{{{_gpu_label(n, g)}}} {max(0.0, min(1.0, v)):.3f}')

    metric("DCGM_FI_PROF_DRAM_ACTIVE", "Ratio of cycles the device memory interface is active (0.0-1.0).")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            v = (util / 100) * 0.70 + random.gauss(0, 0.03)
            lines.append(f'DCGM_FI_PROF_DRAM_ACTIVE{{{_gpu_label(n, g)}}} {max(0.0, min(1.0, v)):.3f}')

    metric("DCGM_FI_PROF_NVLINK_TX_BYTES", "NVLink transmit bandwidth (bytes/s).", "counter")
    for n in range(NODE_COUNT):
        for g in range(GPUS_PER_NODE):
            util = _simulate_util(t, n, g)
            bw = (util / 100) * 300e9 + random.gauss(0, 1e9)
            lines.append(f'DCGM_FI_PROF_NVLINK_TX_BYTES{{{_gpu_label(n, g)}}} {max(0, bw):.0f}')

    return "\n".join(lines) + "\n"


def _updater():
    global _metrics_text
    while True:
        text = _build_metrics()
        with _metrics_lock:
            _metrics_text = text
        time.sleep(SCRAPE_INTERVAL)


# ─── HTTP handler ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            with _metrics_lock:
                body = _metrics_text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/health", "/healthz"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # suppress access logs
        pass


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(
        f"mock-dcgm-exporter starting: "
        f"{NODE_COUNT} nodes × {GPUS_PER_NODE} GPUs ({GPU_MODEL}), "
        f"port={PORT}, interval={SCRAPE_INTERVAL}s"
    )

    # Pre-generate once before server starts
    with _metrics_lock:
        _metrics_text = _build_metrics()

    t = threading.Thread(target=_updater, daemon=True)
    t.start()

    server = HTTPServer(("", PORT), Handler)
    server.serve_forever()
