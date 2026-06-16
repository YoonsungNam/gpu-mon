# Service Discovery

## Overview

gpu-mon monitors GPU efficiency, AI service performance, and workload metadata across
baremetal, Kubernetes, and VMware environments. "Service discovery" in this context goes
beyond finding scrape endpoints — it covers everything gpu-mon needs to discover in order
to answer questions like:

- How efficiently is each GPU being used? (metric targets)
- Which team's job is running on that GPU? (workload identity)
- How many GPUs does that team have allocated vs. actually using? (organizational demand)

gpu-mon organizes discovery into four layers:

| Layer | What is Discovered | Purpose | Collection Model |
|---|---|---|---|
| **1. Infrastructure** | Physical/virtual GPU resources | Know what exists | API polling → ClickHouse |
| **2. Metric Targets & Log Sources** | Exporters, inference endpoints, log sources | Collect efficiency data | Pull (metrics) / Push (logs) |
| **3. Workload Identity** | Jobs, pods, services on GPUs | Know who is using what | API polling / K8s SD |
| **4. Organizational Allocation** | Quotas, fairshare, demand requests | Compare demand vs. usage | API polling / ingestion |

These layers combine to produce capacity insights:

```
Layer 4  Team A is allocated 32 GPUs (quota)
Layer 3  Team A is running 20 GPUs worth of jobs (actual allocation)
Layer 2  Those 20 GPUs average 45% utilization (efficiency)
Layer 1  The cluster has 128 total GPUs, 96 allocated (capacity)
  →  Team A uses 62% of quota, at 45% efficiency — candidate for redistribution
```

## Layer 1: Infrastructure Discovery

**Goal**: Maintain an accurate inventory of GPU resources that exist across all environments.

### Node Capacity (IBS Nodes)

The Metadata Collector polls the batch scheduler API every 120s to track per-node GPU state.

| Field | Description |
|---|---|
| `gpu_total` | Total GPUs on the node |
| `gpu_allocated` | Currently allocated GPUs |
| `status` | idle, alloc, drain, down |

Storage: `s2_nodes` table (ReplacingMergeTree — latest per node). Schema: `schemas/s2_nodes.sql`.

### Pool Capacity (IBS Pools)

Logical node pools define GPU capacity boundaries. Polled every 600s.

| Field | Description |
|---|---|
| `node_list` | Nodes belonging to pool |
| `gpu_total` | Total GPU count in pool |
| `metadata` | Scheduling policy, GPU type, constraints (JSON) |

Storage: `s2_pools` table. Schema: `schemas/s2_pools.sql`.

### VMware VM Inventory

The Metadata Collector polls vCenter every 300s to discover GPU-equipped VMs.

| Field | Description |
|---|---|
| `vm_name`, `vm_uuid` | VM identification |
| `esxi_host`, `cluster`, `resource_pool` | Placement |
| `gpu_count`, `gpu_type`, `gpu_profile` | GPU allocation (passthrough/vGPU) |

Storage: `vmware_vm_inventory` table (12-month TTL). Schema: `schemas/vmware_vm_inventory.sql`.

### GPU Inventory (Planned)

A `gpu_inventory` table is planned to track per-GPU asset details (model, memory, driver
version, CUDA version) via a Custom Ingestion API. Not yet implemented.

## Layer 2: Metric Targets and Log Sources

**Goal**: Discover all endpoints to scrape and log sources to collect for GPU efficiency
and AI service monitoring.

### Metric Targets (Pull)

Central vmagent scrapes all metric endpoints and remote-writes to VictoriaMetrics.

| Target | Port | Metrics | SD Method |
|---|---|---|---|
| **DCGM Exporter** | :9400 | GPU utilization, temperature, power, memory, SM/Tensor Core activity (L1+L2) | File SD (baremetal/VM), K8s SD |
| **node_exporter** | :9100 | CPU, memory, disk, network | File SD (baremetal/VM), K8s SD |
| **Inference engine** | :8080+ | TTFT, TPOT, ITL, KV cache utilization, queue depth, SLA violations | File SD or K8s SD |
| **kube-state-metrics** | (cluster) | Pod/service/resource status | K8s SD (role=service) |

Scrape interval: 15s (configurable in `environments/<env>/vmagent.yaml`).

### Log Sources (Push)

Logs use a push model. Node-level Vector agents forward to a central Vector Aggregator,
which normalizes and writes to ClickHouse.

| Source | Content | Collection |
|---|---|---|
| DCGM / driver logs | XID errors, GPU health events | Vector Agent (systemd or DaemonSet) |
| NCCL communication logs | AllReduce timing, cross-GPU overhead | Parsed via VRL transforms |
| System logs | Kernel, driver messages | Vector Agent |
| Application logs | Inference engine logs, custom apps | Vector Agent |

Pipeline: Node Vector Agent → Central Vector Aggregator → ClickHouse (`gpu_unified_logs`).

Standard log labels: `deployment_env`, `platform`, `cluster_id`, `node_id`, `source`.

## Layer 3: Workload Identity Discovery

**Goal**: Know which job, pod, or service is running on each GPU, so metrics can be
attributed to teams and users.

### Batch Scheduler Jobs (IBS)

The Metadata Collector polls the batch scheduler API every 60s for running/pending jobs.

| Field | Description |
|---|---|
| `job_id`, `job_name` | Job identification |
| `user_id`, `team` | Owner attribution |
| `node_list` | Nodes where the job runs (e.g. `["gpu-node-01", "gpu-node-03"]`) |
| `gpu_indices` | GPU indices per node (e.g. `[0, 1, 2, 3]`) |
| `gpu_count` | Total GPUs allocated |
| `status` | running, pending, completed, failed, cancelled |

Storage: `s2_jobs` table (time-series, 6-month TTL). Schema: `schemas/s2_jobs.sql`.

**Correlation key**: `node_list` + `gpu_indices` map directly to DCGM metric labels
`{node, gpu}`. Since IBS uses exclusive GPU allocation, all GPU activity on those indices
belongs to the job.

### Kubernetes Workloads

In K8s environments, vmagent uses Kubernetes SD to auto-discover pods and services.
Relabel configs propagate workload identity into metric labels:

- `__meta_kubernetes_pod_node_name` → `node`
- `__meta_kubernetes_pod_label_*` → pod labels (app, service name)
- `__meta_kubernetes_namespace` → `namespace`

Config: `environments/<env>/vmagent.yaml` → `kubernetes_sd_configs`.

### Inference Services

Inference endpoints (vLLM, TGI, Triton, etc.) are discovered via File SD or K8s SD.
Their `/metrics` endpoints expose SLO metrics:

- **Latency**: TTFT (Time-to-First-Token), TPOT (Time-per-Output-Token), ITL, E2E latency
- **Throughput**: tokens/sec, requests/sec
- **Resource pressure**: KV cache utilization, queue depth, batch size
- **Reliability**: SLA violation rate, error rate

## Layer 4: Organizational Allocation and Demand

**Goal**: Track how many GPUs each team is allocated (demand/quota) vs. how many they
actually use, enabling capacity planning and resource redistribution.

### Project Quotas (IBS Projects)

The Metadata Collector polls project configurations every 600s.

| Field | Description |
|---|---|
| `project_id`, `project_name` | Project identification |
| `fairshare_weight` | Scheduling priority weight |
| `gpu_limit` | Hard GPU quota for the project |
| `metadata` | Full project config (JSON) |

Storage: `s2_projects` table (ReplacingMergeTree — latest per project). Schema: `schemas/s2_projects.sql`.

### Demand-to-Usage Analysis

Combining Layer 4 (allocation) with Layer 3 (workload) and Layer 2 (metrics) enables
queries such as:

**Allocation vs. actual usage per project:**

```sql
SELECT p.project_id, p.project_name,
       p.gpu_limit                     AS quota_gpus,
       sum(j.gpu_count)               AS running_gpus,
       round(sum(j.gpu_count) / p.gpu_limit * 100, 1) AS usage_pct
FROM s2_projects FINAL AS p
LEFT JOIN s2_jobs AS j ON j.team = p.project_name AND j.status = 'running'
GROUP BY p.project_id, p.project_name, p.gpu_limit;
```

**GPU-hours per team (capacity planning):**

```sql
SELECT team,
       countDistinct(job_id)           AS job_count,
       sum(gpu_count) * 60 / 3600     AS gpu_hours
FROM s2_jobs
WHERE status = 'running'
  AND collected_at >= now() - INTERVAL 24 HOUR
GROUP BY team
ORDER BY gpu_hours DESC;
```

**Pool utilization:**

```sql
SELECT p.pool_id, p.pool_name,
       p.gpu_total                     AS total_gpus,
       sum(n.gpu_allocated)           AS allocated_gpus,
       round(sum(n.gpu_allocated) / p.gpu_total * 100, 1) AS util_pct
FROM s2_pools FINAL AS p
JOIN s2_nodes FINAL AS n ON has(p.node_list, n.node_id)
GROUP BY p.pool_id, p.pool_name, p.gpu_total;
```

### GPU Demand Tracking (Planned)

A `gpu_demand` table and Custom Ingestion API are planned to accept external demand
requests (team, GPU type, count, priority). This will enable demand forecasting and
proactive capacity planning. Not yet implemented.

## Discovery Mechanisms

### File-based Service Discovery (D19)

Used for baremetal and VM nodes where Kubernetes SD is unavailable and Consul/DNS SD
is impractical (e.g. airgap environments).

**How it works:**

```
environments/<env>/targets/*.json     Operators edit these
         |
         v
helmfile.yaml.gotmpl  --(exec cat)-->  fileSD.data values
         |
         v
chart configmap-filesd.yaml  -------->  ConfigMap (Helm-managed)
         |
         v
deployment.yaml  -------------------->  volume mount at /etc/vmagent/sd/
```

**Target file format** (example from `environments/corp.example/targets/baremetal-gpu-nodes.json.example`):

```json
[
  {
    "targets": ["gpu-node-01:9400", "gpu-node-02:9400", "gpu-node-03:9400"],
    "labels": {
      "deployment_env": "corp",
      "platform": "baremetal",
      "cluster": "YOUR_CLUSTER_NAME",
      "workload_type": "training"
    }
  }
]
```

**Operator workflow**: edit target JSON files in `environments/<env>/targets/`, run `helmfile sync`.

**Refresh**: vmagent re-reads files every 60s (`-promscrape.fileSDCheckInterval`).

### Kubernetes Service Discovery

Used in K8s clusters. vmagent watches the Kubernetes API for pods and services matching
label selectors.

Example from `environments/homelab/vmagent.yaml`:

```yaml
- job_name: "node-exporter"
  kubernetes_sd_configs:
    - role: pod
      namespaces:
        names: ["monitoring"]
  relabel_configs:
    - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_name]
      regex: "node-exporter"
      action: keep
    - source_labels: [__meta_kubernetes_pod_node_name]
      target_label: node
```

No manual target files needed — pods are discovered automatically as they start/stop.

### Static Configs

For known, fixed endpoints (e.g. mock exporter in homelab):

```yaml
- job_name: "mock-dcgm"
  static_configs:
    - targets: ["mock-dcgm-exporter.monitoring.svc:9400"]
      labels:
        deployment_env: homelab
        workload_type: training
```

### API-based Metadata Polling (Phase 3)

The Metadata Collector polls external APIs on a schedule to populate ClickHouse tables.
This is not scrape-target discovery, but workload/infrastructure metadata discovery.

| Source | Adapter | Interval | Target Tables |
|---|---|---|---|
| IBS (batch scheduler) | S2 Adapter | 60–600s | s2_jobs, s2_nodes, s2_projects, s2_pools |
| VMware vCenter | VMware Adapter | 300s | vmware_vm_inventory |
| Custom Ingestion API | (planned) | on-demand | gpu_demand, gpu_inventory |

Config: `charts/metadata-collector/values.yaml`.

### Vector Agent Configuration

Log source discovery is configured per node:

- **Baremetal/VM**: Ansible-managed Vector config (systemd service)
- **K8s**: DaemonSet with ConfigMap

Source selection varies by environment — Ansible roles determine which log sources to
collect on each node class.

## Correlation: Connecting the Layers

### Standard Labels

All metric targets use a consistent label set to enable cross-layer correlation:

| Label | Description | Source |
|---|---|---|
| `deployment_env` | Environment (corp, homelab) | SD config labels |
| `platform` | baremetal, k8s, vm | SD config labels |
| `cluster` | Cluster identifier | SD config labels |
| `node` | Node hostname | SD label or relabel from `__address__` |
| `gpu` | GPU index | DCGM exporter |
| `gpu_model` | GPU model name (H100, A100) | DCGM exporter |
| `workload_type` | training, inference | SD config labels |

### Enrichment Methods

Three approaches to join workload metadata with GPU metrics:

| Method | How | Phase |
|---|---|---|
| **Grafana query-time JOIN** | Panel A queries VictoriaMetrics (GPU util by node/gpu), Panel B queries ClickHouse (job/team by node) — displayed together | Phase 3 |
| **Grafana transformations** | Merge + Join transform combines VictoriaMetrics and ClickHouse query results in a single table | Phase 3 |
| **vmagent metric relabeling** | Metadata Collector exposes GPU↔Job mapping; vmagent adds job/team labels directly to DCGM metrics | Phase 6 |

## Adding a New Discovery Target

| Layer | What to Add | Where |
|---|---|---|
| **Infrastructure** | New metadata adapter in Metadata Collector, new ClickHouse table in `schemas/` | `charts/metadata-collector/`, `schemas/` |
| **Metric target** | New scrape job in vmagent config; for non-K8s targets, add target JSON to `environments/<env>/targets/` | `environments/<env>/vmagent.yaml`, `environments/<env>/targets/` |
| **Log source** | New Vector source in node agent config | Ansible role or DaemonSet ConfigMap |
| **Org allocation** | New metadata adapter or extend Custom Ingestion API; new ClickHouse table | `charts/metadata-collector/`, `schemas/` |
