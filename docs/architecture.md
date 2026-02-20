# Architecture

## Overview

gpu-mon is a unified GPU monitoring platform for multi-environment clusters.

```
DATA SOURCES (per node: Exporter + lightweight Vector Agent)
  ├── Baremetal GPU Clusters (S2 Batch Scheduler)
  │     DCGM Exporter (:9400) + node_exporter (:9100) + Vector Agent
  ├── K8s GPU Clusters
  │     DCGM Exporter (DaemonSet) + node_exporter + Vector Agent + kube-state-metrics
  └── GPU VMs (VMware vCenter)
        DCGM Exporter (:9400) + node_exporter + Vector Agent

COLLECTION LAYER (K8s)
  ├── vmagent (Central HA)   — Pull scrapes all Exporters → VictoriaMetrics
  ├── Vector Aggregator      — Receives log Push → normalizes → ClickHouse
  └── Metadata Collector     — Polls S2 API + vCenter API → ClickHouse

STORAGE LAYER (K8s)
  ├── VictoriaMetrics Cluster — Time-series metrics (DCGM, node, inference)
  └── ClickHouse Cluster      — Logs, metadata, profiling traces

PRESENTATION
  └── Grafana                 — Unified dashboards, dual data sources
```

## Key Design Decisions

| ID | Decision | Rationale |
|---|---|---|
| D1 | vmagent over Prometheus | VictoriaMetrics ecosystem, lower resource usage |
| D2 | Vector over Fluent Bit | Native ClickHouse sink, VRL transform language |
| D15 | DCGM Profiling ↔ CUPTI conflict managed via L2 pause/resume | Can't run both simultaneously |
| D18 | Hybrid Pull (metrics) + Push (logs) | Pull is natural for point-in-time metrics; logs are streams |
| D19 | File-based Service Discovery (Ansible-managed JSON) | Decouples scrape config from Ansible, supports 100+ nodes |

## Metric Depth Levels

| Level | Source | Overhead | Purpose |
|---|---|---|---|
| L1 | DCGM basic counters | ~0% | GPU health: Util, Temp, Power, XID |
| L2 | DCGM Profiling, inference /metrics, NCCL logs | 1-3% | Efficiency: Tensor Core, SM Occupancy, TTFT/TPOT |
| L3 | CUPTI, Nsight | 3-20% | On-demand kernel-level profiling |

## Environments

| Env | Orchestration | GPU Source | Purpose |
|---|---|---|---|
| macbook | Docker Compose | mock-dcgm-exporter | Rapid local development |
| homelab | K8s (Helmfile) | mock-dcgm-exporter | Integration testing |
| corp | K8s (Helmfile) | Real DCGM + S2 + vCenter | Production (private repo) |

## Two-Repo Structure

Public repo (`gpu-mon`): all env-agnostic code, charts, src, schemas, dashboards.
Private repo (`gpu-mon-corp`): corp-specific configs only, symlinked at deploy time.

See [corp-deployment.md](corp-deployment.md) for the airgap deployment guide.
