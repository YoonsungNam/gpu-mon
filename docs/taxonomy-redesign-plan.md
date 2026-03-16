# Configuration File Taxonomy Redesign

## Context

During review of the corp deployment configuration, naming inconsistencies were found
where File SD files, Ansible groups, and metric labels mixed different conceptual axes
(platform vs workload vs runtime). This document defines the canonical naming convention
for the gpu-mon project.

## Core Principles

- **Monitoring unit = host x port** — a single physical server may expose multiple exporters
- **File SD file 1:1 Ansible group** — each file is generated from exactly one Ansible group
- **Names follow scrape role**, not workload type

## Naming Convention

| File SD File | Ansible Group | Port | Scrape Target |
|---|---|---|---|
| `baremetal-gpu-nodes.json` | `baremetal_gpu_nodes` | :9400 (+:9100 port rewrite) | Baremetal DCGM exporter + node-exporter |
| `vm-gpu-nodes.json` | `vm_gpu_nodes` | :9400 | VMware VM DCGM exporter |
| `inference-servers.json` | `inference_servers` | :8080 | Inference app metrics |

## Label Renames

| Before | After | Reason |
|---|---|---|
| `env` | `platform` | `env` reads as prod/staging; actual values are baremetal/vmware |
| `server_type` | `serving_runtime` | vllm/triton are serving runtimes, not server types |

## vmagent Job Mapping

- **baremetal-dcgm** job: scrapes `baremetal-gpu-nodes.json` on :9400
- **baremetal-node-exporter** job: scrapes same file with port-rewrite relabel to :9100
- **vm-dcgm** job: scrapes `vm-gpu-nodes.json` on :9400
- **k8s-inference-pods** job: uses `serving_runtime` label (renamed from `server_type`)

## Cross-Repo Sync Checklist

- Corp repo File SD files, Ansible inventory, and vmagent config must follow this naming
- `corp.example/` templates in the public repo must use the same naming
- Grafana dashboards and alerting rules referencing `env` label must migrate to `platform`

## Validation

1. All JSON files must be valid Prometheus File SD format
2. vmagent SD paths must match actual target filenames
3. Ansible group names, File SD files, and vmagent jobs must cross-reference consistently
4. README directory trees must reflect actual file structure
