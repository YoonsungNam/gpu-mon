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
- **`platform` label describes infrastructure**, not deployment environment

## Platform Taxonomy

The `platform` label describes the infrastructure platform where the monitored target runs.
The `environments/` directory structure is orthogonal — it describes *where the stack is deployed*
(macbook, homelab, corp), not the platform of the monitored nodes.

| `platform` value | Meaning | Environments |
|---|---|---|
| `baremetal` | Physical GPU nodes, Ansible-managed | corp |
| `vm` | VMware VM GPU nodes, Ansible-managed | corp |
| `k8s` | K8s-native workloads (real or mock pods) | corp, homelab |
| `docker` | Docker Compose local stack | macbook |

Examples:
- Corp baremetal DCGM targets → `platform: "baremetal"`
- Corp VMware VM DCGM targets → `platform: "vm"`
- Homelab mock DCGM exporter pod → `platform: "k8s"`
- Macbook mock DCGM exporter container → `platform: "docker"`

## File SD Naming Convention

| File SD File | Ansible Group | Port | Scrape Target |
|---|---|---|---|
| `baremetal-gpu-nodes.json` | `baremetal_gpu_nodes` | :9400 (+:9100 port rewrite) | Baremetal DCGM exporter + node-exporter |
| `vm-gpu-nodes.json` | `vm_gpu_nodes` | :9400 | VMware VM DCGM exporter |
| `inference-servers.json` | `inference_servers` | :8080 | Inference app metrics |

## Label Renames

| Before | After | Reason |
|---|---|---|
| `env` | `platform` | `env` reads as prod/staging; actual values describe infrastructure platform |
| `server_type` | `serving_runtime` | vllm/triton are serving runtimes, not server types |

## vmagent Job Mapping

- **baremetal-dcgm** job: scrapes `baremetal-gpu-nodes.json` on :9400
- **baremetal-node-exporter** job: scrapes same file with port-rewrite relabel to :9100
- **vm-dcgm** job: scrapes `vm-gpu-nodes.json` on :9400
- **k8s-inference-pods** job: uses `serving_runtime` label (renamed from `server_type`)

## Changes Per Environment

### corp (via corp.example/ templates)
- Rename `gpu-nodes.json` → `baremetal-gpu-nodes.json`
- Labels: `env` → `platform` (values: `baremetal`, `vm`)
- Ansible: `[gpu_nodes]` → `[baremetal_gpu_nodes]`, add `[vm_gpu_nodes]`

### homelab
- Keep `targets/gpu-nodes.json` as a K8s mock-exporter target file; do not rename it to `baremetal-gpu-nodes.json`
- Labels: `"env": "homelab"` → `"platform": "k8s"` (mock exporter runs as K8s pod)
- vmagent static_configs: `env: homelab` → `platform: k8s`
- Ansible: `[gpu_nodes]` → `[baremetal_gpu_nodes]`, add `[vm_gpu_nodes]` placeholder
- Commented-out File SD path: update to `baremetal-gpu-nodes.json`

### macbook
- No File SD files (Docker Compose, no vmagent File SD)
- If/when mock exporter emits a platform label, use `docker`

### Shared
- Vector agent template: `.env` → `.platform`
- ClickHouse schema: `env` column → `platform` column (requires migration if table exists)

## Cross-Repo Sync Checklist

- Corp repo File SD files, Ansible inventory, and vmagent config must follow this naming
- `corp.example/` templates in the public repo must use the same naming
- Grafana dashboards and alerting rules referencing `env` label must migrate to `platform`

## Validation

1. All JSON files must be valid Prometheus File SD format
2. vmagent SD paths must match actual target filenames
3. Ansible group names, File SD files, and vmagent jobs must cross-reference consistently
4. README directory trees must reflect actual file structure
