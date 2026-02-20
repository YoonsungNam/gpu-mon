# GPU Monitoring System — Claude Code Context

## Project Overview
Unified GPU monitoring platform for multi-cluster environments (Baremetal + K8s + VMware VM).
Collects GPU metrics at 3 depth levels (L1 hardware → L2 efficiency → L3 kernel profiling).

## Architecture Summary
- **Metrics**: Pull-based — Central vmagent scrapes all node exporters
- **Logs**: Push-based — Lightweight Vector agents forward to central aggregator
- **Storage**: VictoriaMetrics (time-series) + ClickHouse (logs/metadata/profiling)
- **Metadata**: Optional integration with batch schedulers and VMware vCenter
- **Orchestration**: Helmfile for multi-environment management

## Repo Rules
- **Language**: All code, comments, docs, and commit messages in English
- **OSS Helm charts**: Referenced via Helmfile (never copied into this repo)
- **Custom Helm charts**: Only in `charts/` (for components without OSS charts)
- **Application source**: Only in `src/`
- **Environment configs**: Only in `environments/`
- **ClickHouse schemas**: Only in `schemas/`
- **No company-specific data**: Production configs live in a separate private repo
- **Corp paths are gitignored**: `environments/corp/`, `**/corp*` — never commit these

## Environments
- **macbook**: Docker Compose (no K8s), mock GPU metrics — for rapid development
- **homelab**: Helmfile + K8s, mock GPU metrics — for integration testing

## Current Phase
Phase 1: Foundation (L1+L2 metric pipeline)

## Tech Stack
- Helmfile, Helm 3
- VictoriaMetrics, ClickHouse, Grafana, Vector, vmagent
- Python (metadata-collector, mock-exporter)
- Ansible (baremetal/VM node agent deployment)

## Key Design Decisions
- D1: vmagent over Prometheus (VM ecosystem, lightweight)
- D2: Vector over Fluent Bit (native ClickHouse sink, VRL transforms)
- D15: DCGM Profiling ↔ CUPTI conflict managed via L2 pause/resume protocol
- D18: Hybrid Pull (metrics) + Push (logs) — see docs/planning-v5.md in corp repo
- D19: File-based Service Discovery (Ansible-managed JSON)
