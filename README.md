# gpu-mon

A unified GPU monitoring platform for multi-cluster environments — Baremetal, Kubernetes, and VMware VMs — built on VictoriaMetrics and ClickHouse.

## Overview

gpu-mon provides end-to-end observability for GPU infrastructure used in AI/ML workloads (training & inference), collecting metrics at three depth levels:

- **L1 — Hardware Counters** (always-on, ~0% overhead): GPU utilization, memory, temperature, power, Xid errors via DCGM/NVML
- **L2 — Efficiency Analysis** (always-on, 1–3% overhead): Tensor Core activity, SM occupancy, HBM bandwidth, NVLink throughput via DCGM Profiling + inference server metrics (TTFT, TPOT, KV Cache)
- **L3 — Kernel Profiling** (on-demand, modular): Per-kernel execution time, memory access patterns via PyTorch Profiler / Nsight Systems

### Architecture

```
GPU Nodes (Baremetal/K8s/VM)          Central (K8s)                Storage
┌─────────────────────┐       ┌──────────────────────┐    ┌─────────────────┐
│ DCGM Exporter :9400 │←Pull──│ vmagent (Central, HA)│───→│ VictoriaMetrics │
│ node_exporter :9100 │←Pull──│ File SD + K8s SD     │    │ (time-series)   │
│ Inference /metrics   │←Pull──│                      │    └─────────────────┘
│                      │       └──────────────────────┘
│ Vector Agent ────Push──→ Vector Aggregator ──────────→ ClickHouse
│ (lightweight forward)│       (parse/transform)       │ (logs/metadata/
└─────────────────────┘                                │  profiling)
                                                       └─────────────────┘
```

**Key design decisions:**
- **Pull-based metrics** — Central vmagent scrapes all node exporters (no per-node vmagent, ~57% node resource reduction)
- **Push-based logs** — Lightweight Vector agents forward raw logs to a central aggregator
- **Standardized labels** — Same exporter + same schema across all environments (`env`, `cluster`, `node`, `gpu`, `workload_type`)
- **Metadata enrichment** — Optional integration with batch schedulers and VM managers to correlate GPU metrics with job/user/team context

## Tech Stack

| Layer | Component | Role |
|---|---|---|
| **Metrics Storage** | VictoriaMetrics (Cluster) | Time-series DB for L1+L2 GPU metrics |
| **Analytics Storage** | ClickHouse (Cluster) | Logs, profiling traces, metadata, JSON analytics |
| **Metrics Collection** | vmagent (Central) | Pull scraper with File SD + K8s SD |
| **Log Collection** | Vector (Agent + Aggregator) | Lightweight push pipeline |
| **Visualization** | Grafana | 9 dashboards (GPU Health, Efficiency, Inference SLA, etc.) |
| **Alerting** | vmalert + Alertmanager | PromQL-based rules with Slack/Email routing |
| **GPU Metrics** | DCGM Exporter | L1+L2 metrics via HTTP endpoint |
| **Orchestration** | Helmfile | Multi-environment Helm release management |

## Repository Structure

```
gpu-mon/
├── helmfile.yaml              # Multi-environment orchestration
├── environments/
│   ├── defaults.yaml          # Shared base values
│   ├── macbook/               # Docker Compose (no K8s)
│   └── homelab/               # Helmfile + K8s
├── charts/                    # Custom Helm charts (components not available as OSS)
│   ├── vmagent-central/       # Central pull scraper with File SD
│   ├── metadata-collector/    # Scheduler/VM metadata aggregator
│   └── mock-dcgm-exporter/   # Fake GPU metrics for testing
├── src/                       # Application source code
│   ├── metadata-collector/
│   └── mock-dcgm-exporter/
├── schemas/                   # ClickHouse DDL (11 tables)
├── dashboards/                # Grafana dashboard JSON
├── alerting/                  # vmalert rules + Alertmanager config
├── compose/                   # macbook Docker Compose stack
├── ansible/                   # Baremetal/VM node agent deployment
├── docs/                      # Architecture docs & ADRs
└── scripts/                   # Setup, build, bundle utilities
```

### Environment Strategy

| Environment | Tool | K8s | GPU | Purpose |
|---|---|---|---|---|
| **macbook** | Docker Compose | ✗ | Mock | Rapid iteration: dashboards, schemas, app logic |
| **homelab** | Helmfile | ✅ | Mock | Integration testing: Helm charts, alerts, K8s SD |

> **Extending to production:** This repo is designed to be extended with a private overlay repo for production-specific configurations (registry URLs, node inventories, secrets). See [docs/extending-to-production.md](docs/extending-to-production.md) for details.

## Quick Start

### macbook (Docker Compose)

```bash
# Prerequisites: Docker Desktop
cd compose/
docker compose up -d

# Access:
#   Grafana:          http://localhost:3000
#   VictoriaMetrics:  http://localhost:8428
#   ClickHouse:       http://localhost:8123
```

### homelab (Helmfile + K8s)

```bash
# Prerequisites: K8s cluster, helm, helmfile, helm-diff plugin
helmfile -e homelab sync

# Verify
kubectl -n monitoring get pods
```

## Extending to Production

This repo provides a clean separation between public infrastructure code and private environment-specific configurations. To deploy in a production or air-gapped environment:

1. Create a private repo with your environment-specific values
2. Symlink it into `environments/<your-env>/`
3. Run `helmfile -e <your-env> sync`

The architecture supports:
- **Air-gapped deployment** via `scripts/airgap-bundle.sh`
- **Batch scheduler metadata integration** (adapter pattern in `src/metadata-collector/`)
- **VMware vCenter inventory** collection
- **File-based Service Discovery** managed by Ansible for non-K8s nodes

See [docs/extending-to-production.md](docs/extending-to-production.md) for the full guide.

## Dashboards

| Dashboard | Data Source | Description |
|---|---|---|
| GPU Health (L1) | VictoriaMetrics | Utilization, memory, temperature, power, Xid errors |
| GPU Efficiency (L2) | VictoriaMetrics | Tensor Core active, SM occupancy, HBM bandwidth |
| Inference SLA (L2) | VictoriaMetrics | TTFT, TPOT, KV Cache utilization, queue length |
| Training Communication (L2) | ClickHouse | NCCL AllReduce timing, communication/compute ratio |
| Profiling Analysis (L3) | ClickHouse | Per-kernel analysis, top-K kernels, session comparison |
| Job Explorer | ClickHouse + VM | Job↔GPU mapping, per-team GPU usage (requires metadata integration) |
| VM GPU Inventory | ClickHouse | VMware GPU VM list, ESXi host distribution (requires vCenter integration) |
| Demand & Capacity | ClickHouse | GPU demand trends, team usage, inventory |
| System Overview | VictoriaMetrics | Node health across all environments |

## Phased Rollout

| Phase | Scope | Duration |
|---|---|---|
| **Phase 1** | Foundation — L1+L2 metric pipeline (Pull-based) | 2–3 weeks |
| **Phase 2** | Log pipeline + inference server metrics | 2–3 weeks |
| **Phase 3** | Analytics + metadata integration + DCGM Job Stats | 3–4 weeks |
| **Phase 4** | Alerting & hardening | 1–2 weeks |
| **Phase 5** | L3 modular profiling (CUPTI conflict management) | 3–4 weeks |
| **Phase 6** | Advanced: metric enrichment, Kafka, auto-optimization | Ongoing |

## License

MIT
