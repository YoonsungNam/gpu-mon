# GPU Monitoring System Planning Document (v5)

> **Goal**: Build a unified monitoring system on K8s for multi GPU Cluster and GPU VM environments
> **Confirmed DBs**: ClickHouse (logs/analytics/profiling/metadata), VictoriaMetrics (time-series metrics)
> **Existing Environment**: Baremetal - Zabbix + DCGM, VMware vSphere GPU VM, Internal Batch Scheduler (IBS)
> **Core Principles**:
> - Standardize with the same Exporter + same schema across all environments (Baremetal, K8s, VM)
> - **Metric collection is Pull-based** (Central vmagent scrapes each node's Exporter)
> - **Log collection is lightweight Push** (Node Vector Agent → Central Vector Aggregator)
> - Systematize GPU/AI metrics into 3 depth levels (L1~L3), with L3 as modular on-demand
> - Collect legacy system metadata (VMware VM, IBS Job/Node/Project/Pool) and combine (Enrich) with GPU metrics

### v5 Change History (compared to v4)

| Change | Affected Sections |
|---|---|
| **[Core] Changed metric collection from Push → Pull-based** — Removed node vmagent, central vmagent (K8s) directly scrapes all node Exporters | 2, 5, 6, 7, 9, 13 |
| **[Core] Changed log collection to lightweight Push** — Node Vector Agent (lightweight) → Central Vector Aggregator (K8s) handles parsing/transformation | 2, 5, 6, 7, 9, 13 |
| **Reduced per-node agents from 4 → 2~3** (removed vmagent, lightweight Vector) | 2, 9 |
| **File-based Service Discovery** — Manage scrape targets via Ansible-managed target files | 2 |
| Complete revision of architecture diagram — IBS/VMware integrated into Data Sources, Path A (Pull) / B (Push) notation | 5 |
| Added DCGM Profiling ↔ CUPTI conflict management strategy | 3 |
| IBS metadata storage strategy classified into 3 types (time-series/current value/snapshot), added ibs_projects/ibs_pools tables | 4, 8 |
| Added Per-Job GPU Utilization measurement strategy, introduced DCGM Job Stats (L2.5) | 3, 4 |
| Deprioritized Module C (CUPTI Wrapper) | 3, 6 |
| Added design decisions D15~D18 | 12 |

### Previous Version Change History

<details>
<summary>v4 Change History (compared to v3)</summary>

| Change | Affected Sections |
|---|---|
| [New] Section 4: Legacy System Metadata Integration — VMware vCenter GPU VM Inventory, Internal Batch Scheduler (IBS) Job/Node metadata collection system | 4 (new) |
| Added Metadata type to data classification | 1 |
| Added Metadata Collector to agent matrix | 2 |
| Added Enrichment labels to standard labels | 2 |
| Added Legacy Metadata Sources + Metadata Collector to architecture | 5 |
| Added ClickHouse tables (`ibs_jobs`, `ibs_nodes`, `vmware_vm_inventory`) | 8 |
| Merged metadata integration into roadmap Phase 3 | 10 |
| Added Job Explorer, VM Inventory to dashboards | 11 |
| Added design decisions D12~D14 | 12 |

</details>

---

## 1. Data Classification and Storage Strategy

We classify monitoring target data into **5 types** and map each to the optimal storage.

| Data Type | Examples | Storage | Reason |
|---|---|---|---|
| **Time-Series Metrics** | GPU Util, Memory, Temp, Power, Tensor Active, Inference Latency/Throughput | **VictoriaMetrics** | High-performance time-series DB, Prometheus-compatible, favorable for long-term retention |
| **Logs (structured/unstructured)** | Job execution logs, system logs, OOM/Xid error logs, NCCL communication logs | **ClickHouse** | Columnar DB enables fast analytical queries on large volumes of logs |
| **JSON/Analytical Data** | GPU demand data, cluster inventory, SLA reports | **ClickHouse** | JSON column type support, optimal for complex analytical queries |
| **Profiling Traces** | Kernel execution time, memory access patterns, NCCL operations, operator analysis | **ClickHouse** | Optimal for large-scale event analysis and per-session aggregation |
| **Legacy Metadata** *(v4 new)* | VMware VM Inventory, IBS Job metadata, IBS Node status | **ClickHouse** | Optimal for snapshot-based history management and JOIN analysis with GPU metrics |

### Why Split It This Way?

- **VictoriaMetrics** is specialized for Prometheus-style time-series data, efficiently storing/querying GPU metrics and inference server metrics collected at sub-second intervals.
- **ClickHouse** is an OLAP DB that handles high-volume INSERT of logs, JSON data, and profiling traces, and performs fast analytical queries (aggregation, filtering).
- **Legacy Metadata** is **snapshot/event-based** data, not time-series. To answer questions like "What Job is running on this GPU right now?" or "Which ESXi host is this VM on?", it needs to be JOINable with time-series metrics in ClickHouse.
- By separating the two DBs, we achieve optimal performance matched to each workload's characteristics.

### Why Legacy Metadata Is Needed (v4 Core Motivation)

Current limitations of GPU monitoring:

```
What DCGM shows:               What we actually want to know:
─────────────────              ─────────────────
GPU 0: Util 85%                 "User A's LLaMA training Job is
GPU 0: Temp 72°C                 running on GPU 0~3 of gpu-node-03
GPU 0: Tensor Active 0.62        in IBS queue 'high-priority'.
                                  Current IBS Job ID: 84723"

What GPU VM shows:              What we actually want to know:
─────────────────              ─────────────────
VM-01: GPU Util 45%              "vm-gpu-research-07 (GPU Passthrough A100) is
VM-01: Memory 32GB/40GB           running on ESXi host esxi-gpu-02.internal.
                                   vCenter Resource Pool: pool-a"
```

**By collecting metadata**, GPU metrics gain context, significantly improving the quality of operations and analysis.

---

## 2. Per-Environment Standardization Strategy

### 2.1 Core Principle: Pull-based Collection, Same Exporter, Same Schema

Regardless of which environment the data comes from (Baremetal, K8s, VM), we **expose via the same Exporter and store with the same schema**.

```
v5 Core Change: Push → Pull (Hybrid)

  Before (v4, Push-based):
    Each GPU node: DCGM Exporter + node_exporter + vmagent + Vector (4 agents)
    → vmagent performs local scrape → remote_write to central VictoriaMetrics (Push)
    → Vector collects logs → direct sink to central ClickHouse (Push)

  After (v5, Hybrid Pull):
    Each GPU node: DCGM Exporter + node_exporter + Vector Agent (2~3)
    → vmagent removed! Exporter only exposes HTTP endpoint (awaiting Pull)
    → Central vmagent (K8s) directly scrapes all nodes' :9400/:9100 (Pull)
    → Vector Agent is lightweight, forwarding only logs to central Aggregator (Push maintained)

  Why Hybrid?
    • Metrics: "current value" snapshot → Pull is natural
      (missing a 10-second-old value is fine, next scrape brings a new value)
    • Logs: "event stream" → Push is natural
      (a log event that passes once won't come again, push from local to prevent loss)
```

### 2.2 Per-Environment Deployment Matrix (v5)

**Agents deployed on nodes (Exporter + lightweight Agent):**

| Agent | Role | Baremetal | K8s | VM | Notes |
|---|---|---|---|---|---|
| **DCGM Exporter** | GPU metrics HTTP exposure (L1+L2) | ✅ systemd | ✅ DaemonSet | ✅ systemd | **Awaiting Pull (:9400)** |
| **node_exporter** | System metrics HTTP exposure | ✅ systemd | ✅ DaemonSet | ✅ systemd | **Awaiting Pull (:9100)** |
| **Vector Agent** | Log collection → forward to central Aggregator | ✅ systemd | ✅ DaemonSet | ✅ systemd | **Lightweight Push (forward only)** |
| ~~**vmagent**~~ | ~~Metrics scrape → VM send~~ | ❌ removed | ❌ removed | ❌ removed | **Removed in v5** |

> **Change from v4**: Removed vmagent from nodes. Metric collection control moved to central.

**Components deployed centrally (K8s):**

| Component | Role | Deployment Method | Notes |
|---|---|---|---|
| **vmagent (Central)** | Pull scrape all node Exporters → VictoriaMetrics | K8s Deployment (HA) | **v5 new** |
| **Vector Aggregator** | Receive logs from node Vector Agents → parse/transform → ClickHouse | K8s Deployment | **v5 new** |
| **Metadata Collector** | Poll IBS + VMware APIs → ClickHouse | K8s Deployment | Same as v4 |

Additional agents (per environment/role):

| Agent | Baremetal | K8s | VM | Notes |
|---|---|---|---|---|
| **kube-state-metrics** | - | ✅ Deployment | - | K8s only (Pod/Node status), scraped by central vmagent |
| **Inference server /metrics** | ✅ | ✅ | ✅ | vLLM/TGI/Triton self-exposed, **Pulled by central vmagent** |
| **Zabbix Agent** | ✅ maintained | - | - | IPMI/HW/SNMP only (reduced role) |

**Per-node agent resource comparison:**

```
v4 (Push, 4 agents per node):
  DCGM Exporter:  0.10 core,  128Mi
  node_exporter:  0.05 core,   64Mi
  vmagent:        0.25 core,  256Mi  ← removed in v5
  Vector:         0.25 core,  256Mi  ← made lightweight in v5
  Total:          0.65 core,  704Mi

v5 (Pull, 2~3 agents per node):
  DCGM Exporter:  0.10 core,  128Mi  (same)
  node_exporter:  0.05 core,   64Mi  (same)
  Vector Agent:   0.10 core,  128Mi  (lightweight: forward only, no parsing)
  Total:          0.25 core,  320Mi  (approx. 55% reduction)
```

### 2.3 Zabbix Role Redefinition

The existing Zabbix has its role reduced, and GPU/log collection is migrated to standard agents.

```
Before (current):
  Zabbix = Metric collection + alerting + log monitoring + inventory (all-in-one)

After (changed):
  Zabbix = Baremetal hardware only (limited role)
  └── IPMI sensors (fan speed, PSU status, disk SMART)
  └── Network equipment SNMP (switches, PDUs)
  └── Baremetal asset inventory (serial numbers, rack location, etc. — CMDB role)

  GPU metric collection → DCGM Exporter (exposure) + central vmagent (Pull)
  AI workload metrics  → Inference server /metrics (exposure) + central vmagent (Pull)
  Log collection       → Vector Agent (node) → Vector Aggregator (central)
  Alerting             → vmalert + Alertmanager (standardized)
  Legacy metadata      → Metadata Collector (v4~)
```

### 2.4 Metric Standard Label Schema (VictoriaMetrics)

All environment metrics are assigned the following **standard labels**. Managed centrally in the **central vmagent**'s `relabel_configs`.

| Label | Description | Example Values | Source |
|---|---|---|---|
| `env` | Infrastructure environment | `baremetal`, `k8s`, `vm` | File SD label |
| `cluster` | Cluster identifier | `gpu-cluster-a`, `k8s-prod-01` | File SD label |
| `node` | Node hostname | `gpu-node-01` | SD auto / relabel |
| `gpu` | GPU index | `0`, `1`, `2`, ... | DCGM auto |
| `gpu_model` | GPU model name | `H100`, `A100`, `H200` | DCGM auto |
| `workload_type` | Workload type | `training`, `inference` | File SD label |

**Enrichment Labels (v4~):**

GPU metrics themselves do not contain Job information. Instead, context is provided by **JOINing with metadata tables in ClickHouse at Grafana query time**.

```
Method 1: Real-time JOIN in Grafana (recommended)
   VictoriaMetrics: GPU Util by (node, gpu)
   + ClickHouse: ibs_jobs WHERE node_id = $node AND gpu_indices HAS $gpu AND status = 'running'
   → Display "which Job is running on this GPU" in the dashboard

Method 2: Label injection via central vmagent relabel (optional, complex)
   Metadata Collector exposes GPU↔Job mapping via /metrics
   Central vmagent reads this mapping and dynamically adds job_id labels to DCGM metrics
   → Fits well with Pull architecture (everything controlled centrally)
   → High implementation complexity (Phase 6 enhancement)
```

**Central vmagent Pull Configuration (v5 core):**

```yaml
# Central vmagent config — managed as K8s ConfigMap
# All scrape targets and labels defined here in one place

global:
  scrape_interval: 15s    # Default Pull interval

# Remote storage
remoteWrite:
  - url: "http://vminsert.victoriametrics.svc:8480/insert/0/prometheus/"

scrape_configs:
  # ────────────────────────────────────────
  # Baremetal GPU Clusters (managed via File SD)
  # ────────────────────────────────────────
  - job_name: "baremetal-dcgm"
    scrape_interval: 15s
    file_sd_configs:
      - files: ["/etc/vmagent/sd/baremetal-gpu-nodes.json"]
        refresh_interval: 60s    # File change detection interval
    relabel_configs:
      - source_labels: [__address__]
        regex: "(.+):.*"
        target_label: node

  - job_name: "baremetal-node-exporter"
    file_sd_configs:
      - files: ["/etc/vmagent/sd/baremetal-node-exporters.json"]
        refresh_interval: 60s
    relabel_configs:
      - source_labels: [__address__]
        regex: "(.+):.*"
        target_label: node

  # ────────────────────────────────────────
  # VM GPU Clusters (managed via File SD)
  # ────────────────────────────────────────
  - job_name: "vm-dcgm"
    file_sd_configs:
      - files: ["/etc/vmagent/sd/vm-gpu-nodes.json"]
        refresh_interval: 60s

  # ────────────────────────────────────────
  # K8s Clusters (K8s SD auto)
  # ────────────────────────────────────────
  - job_name: "k8s-dcgm"
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names: ["gpu-monitoring"]
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: "dcgm-exporter"
        action: keep

  - job_name: "k8s-kube-state-metrics"
    kubernetes_sd_configs:
      - role: service
    relabel_configs:
      - source_labels: [__meta_kubernetes_service_label_app]
        regex: "kube-state-metrics"
        action: keep

  # ────────────────────────────────────────
  # Inference Servers (File SD or K8s SD per environment)
  # ────────────────────────────────────────
  - job_name: "inference-servers"
    file_sd_configs:
      - files: ["/etc/vmagent/sd/inference-servers.json"]
        refresh_interval: 60s
```

**File-based Service Discovery (Ansible-managed):**

```json
// /etc/vmagent/sd/baremetal-gpu-nodes.json
// Automatically generated by Ansible playbook when nodes are added/removed
[
  {
    "targets": [
      "gpu-node-01:9400", "gpu-node-02:9400", "gpu-node-03:9400",
      "gpu-node-04:9400", "gpu-node-05:9400"
      // ... 120 nodes
    ],
    "labels": {
      "env": "baremetal",
      "cluster": "gpu-cluster-a",
      "workload_type": "training"
    }
  },
  {
    "targets": [
      "infer-node-01:9400", "infer-node-02:9400"
    ],
    "labels": {
      "env": "baremetal",
      "cluster": "gpu-cluster-a",
      "workload_type": "inference"
    }
  }
]
```

```
File SD Operations Flow:

  1. When nodes are added/removed:
     Ansible playbook → Update JSON file → Update K8s ConfigMap
     vmagent re-reads the file every refresh_interval (60 seconds)
     → Automatically starts scraping new nodes / stops scraping removed nodes

  2. When scrape interval/labels change:
     Modify only the central vmagent config (ConfigMap) in 1 place
     → kubectl rollout restart → Applied globally
     (No need to deploy individually to 120 nodes!)

  3. Checking monitoring target status:
     In vmagent UI (http://vmagent:8429/targets)
     View all scrape targets' status (up/down) and last scrape time
```

This enables unified cross-environment/workload queries in Grafana:

```promql
# GPU utilization across all environments at a glance
avg(DCGM_FI_DEV_GPU_UTIL) by (env, cluster)

# Filter inference workloads only
avg(DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{workload_type="inference"}) by (cluster)
```

### 2.5 Log Standard Schema (ClickHouse)

All environment logs are stored in a **single table with the same schema**. Standardized using Vector's `remap` transforms.

| Field | Type | Description | Example |
|---|---|---|---|
| `timestamp` | DateTime64(3) | Log occurrence time | `2025-07-15 10:23:45.123` |
| `env` | LowCardinality(String) | Infrastructure environment | `baremetal`, `k8s`, `vm` |
| `cluster_id` | LowCardinality(String) | Cluster identifier | `gpu-cluster-a` |
| `node_id` | String | Node hostname | `gpu-node-01` |
| `gpu_id` | Nullable(UInt8) | GPU index (when applicable) | `0` (NULL for system logs) |
| `log_level` | LowCardinality(String) | Log level | `INFO`, `WARN`, `ERROR`, `FATAL` |
| `source` | LowCardinality(String) | Log source | `driver`, `scheduler`, `kubelet`, `system`, `nccl` |
| `message` | String | Log body | `XID error 79 detected` |
| `metadata` | String (JSON) | Environment-specific additional data | `{"pid": 1234, "container": "train-job"}` |

Vector transform example (Baremetal syslog → standard schema):

```toml
[transforms.standardize_baremetal]
type = "remap"
inputs = ["raw_syslog"]
source = '''
  .env = "baremetal"
  .cluster_id = "gpu-cluster-a"
  .node_id = get_hostname!()
  .gpu_id = null
  .log_level = to_syslog_level(.severity) ?? "INFO"
  .source = "system"
  .metadata = encode_json({"facility": .facility, "pid": .pid})
'''
```

Vector transform example (K8s Pod logs → standard schema):

```toml
[transforms.standardize_k8s]
type = "remap"
inputs = ["kubernetes_logs"]
source = '''
  .env = "k8s"
  .cluster_id = "k8s-prod-01"
  .node_id = .kubernetes.node_name
  .gpu_id = null
  .log_level = parse_log_level(.message) ?? "INFO"
  .source = .kubernetes.container_name
  .metadata = encode_json({
    "namespace": .kubernetes.namespace,
    "pod": .kubernetes.pod_name,
    "container": .kubernetes.container_name
  })
'''
```

---

## 3. AI Metric Hierarchy (L1 / L2 / L3)

GPU metrics have **depth**, and L1 alone is insufficient to understand the actual efficiency of AI workloads. A 3-level system is applied considering both Training and Inference.

### 3.1 Metric Depth Overview

```
Level 1: Hardware Counters (always-on collection, overhead ~0%)
├── DCGM / NVML basic metrics
├── GPU Utilization, Memory, Temp, Power, Xid Error
└── "Is the GPU busy?" → Yes/No level

Level 2: Efficiency Analysis (always-on collection, overhead 1~3%)
├── DCGM Profiling Metrics (DCGM_FI_PROF_*)
├── Inference server metrics (vLLM/TGI/Triton /metrics)
├── NCCL communication logs (Vector parsing)
├── SM Occupancy, Tensor Core Active, DRAM Activity, PCIe/NVLink BW
├── TTFT, TPOT, KV Cache, Batch Size, Queue Length
└── "What is the GPU doing? Is the AI workload efficient?"

Level 3: Kernel/Operation Level (on-demand modular, overhead 3~20%)
├── CUPTI Wrapper, Nsight Systems, Nsight Compute, PyTorch Profiler
├── Individual CUDA kernel execution time, memory access patterns, per-operator time
└── "Which kernel is slow and why? Where exactly is the bottleneck?"
```

### 3.2 Why L1 Alone Is Insufficient

```
Example: LLM training on H100

Level 1 (DCGM basic) only:
  GPU Utilization: 95%     ← "Using it well!" ...really?

Level 2 (DCGM Profiling) reveals:
  SM Occupancy: 40%        ← Only 40% of SMs are active
  Tensor Core Active: 25%  ← Only 25% Tensor Core utilization!
  DRAM Read BW: 2.8 TB/s   ← Memory bandwidth is nearly saturated
  → Conclusion: Memory-bound workload. GPU Util 95% means "busy but inefficient"

Level 3 (CUPTI/Nsight) reveals:
  Attention kernel: 45ms (Tensor 80% active)
  AllReduce kernel: 120ms  ← This is the bottleneck!
  Memory copy D2H: 30ms
  → Conclusion: Communication overhead is 60% of total. NCCL optimization or overlap needed
```

### 3.3 Level 1 Metric Details (Always-on, All Environments)

Collected via DCGM Exporter basic counters. Common for training/inference.

| DCGM Metric | Description | Training Significance | Inference Significance |
|---|---|---|---|
| `DCGM_FI_DEV_GPU_UTIL` | GPU utilization | Higher is better | High doesn't mean good (should be low when idle for cost efficiency) |
| `DCGM_FI_DEV_FB_USED` / `FB_FREE` | GPU memory usage | Model + Activation size | KV Cache size (varies with concurrent request count) |
| `DCGM_FI_DEV_GPU_TEMP` | Temperature | Throttling detection | Same |
| `DCGM_FI_DEV_POWER_USAGE` | Power | Energy efficiency | Same |
| `DCGM_FI_DEV_SM_CLOCK` | SM Clock | Throttling detection | Same |
| `DCGM_FI_DEV_PCIE_TX/RX_THROUGHPUT` | PCIe bandwidth | Data loading bottleneck | Input transfer bottleneck |
| `DCGM_FI_DEV_XID_ERRORS` | Xid errors | HW defect detection | Same |

**Collection path**: DCGM Exporter (:9400) → vmagent → VictoriaMetrics
**Storage**: VictoriaMetrics
**Overhead**: ~0%

### 3.4 Level 2 Metric Details (Always-on, Efficiency Analysis)

Level 2 is collected from 3 sources.

#### 3.4.1 DCGM Profiling Metrics (Training + Inference Common)

Can be collected by **enabling Profiling counters** in DCGM Exporter. Activated via custom counter CSV file.

| DCGM Profiling Metric | Description | Training Significance | Inference Significance |
|---|---|---|---|
| `DCGM_FI_PROF_SM_ACTIVE` | SM active ratio | Parallelism indicator | Reflects batch size/concurrent requests |
| `DCGM_FI_PROF_SM_OCCUPANCY` | SM Occupancy | Warp scheduling efficiency | Same |
| `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | Tensor Core utilization | Key efficiency metric | High during Prefill, **very low** during Decode (normal) |
| `DCGM_FI_PROF_PIPE_FP64_ACTIVE` | FP64 pipe utilization | Scientific computing workloads | Nearly 0 (normal) |
| `DCGM_FI_PROF_PIPE_FP32_ACTIVE` | FP32 pipe utilization | Mixed Precision verification | Same |
| `DCGM_FI_PROF_PIPE_FP16_ACTIVE` | FP16 pipe utilization | Mixed Precision verification | Same |
| `DCGM_FI_PROF_DRAM_ACTIVE` | HBM bandwidth utilization | Memory-bound determination | High during Decode (Memory-BW bound) |
| `DCGM_FI_PROF_PCIE_TX/RX_BYTES` | PCIe transfer volume | Data loading | Input/output transfer |
| `DCGM_FI_PROF_NVLINK_TX/RX_BYTES` | NVLink transfer volume | AllReduce communication volume | TP (Tensor Parallel) communication volume |

**Collection path**: DCGM Exporter (:9400, custom counters) → vmagent → VictoriaMetrics
**Storage**: VictoriaMetrics
**Overhead**: 1~3%

#### 3.4.2 Inference Server Metrics (Inference Workload Only)

Inference servers (vLLM, TGI, Triton, etc.) expose Prometheus-format metrics via their own `/metrics` endpoint.

**Latency**: TTFT, TPOT, ITL, E2E Latency, Time in Queue
**Throughput**: Output Tokens/sec, Total Tokens/sec, Requests/sec, Tokens/sec/GPU
**KV Cache**: Utilization, Block Usage, Hit Rate, Eviction Count
**Batching**: Running Batch Size, Queue Length, Preemption Count
**Error/SLA**: Success/Failure Rate, SLA Violation Rate, Timeout, OOM Kill

**Collection path**: Inference server (:8000/metrics etc.) → vmagent → VictoriaMetrics
**Storage**: VictoriaMetrics
**Overhead**: ~0%

#### 3.4.3 NCCL Communication Metrics (Distributed Training Specific)

**Collection path**: NCCL logs → Vector (parsing + structuring) → ClickHouse
**Storage**: ClickHouse (gpu_unified_logs, source='nccl')
**Overhead**: Low (log output level)

### 3.5 Level 3 Metrics: Modular On-Demand Profiling

L3 is a modular approach where profiling is **activated when needed and results are loaded into ClickHouse**.

#### 3.5.1 DCGM Profiling (L2) ↔ CUPTI-based Tools (L3) Conflict Issue

DCGM's L2 Profiling metrics (`DCGM_FI_PROF_*`) and L3 profiling tools both internally use the **NVIDIA CUPTI Activity API**. The CUPTI Activity API has a limit on the number of concurrent subscribers, so **activating L2 and L3 simultaneously causes conflicts**.

```
CUPTI Activity API Conflict Structure:

  DCGM Profiling (L2 always-on)     L3 Profiling Tools
  ━━━━━━━━━━━━━━━━━━━━━━          ━━━━━━━━━━━━━━━━━━
  DCGM_FI_PROF_SM_ACTIVE    ←┐     Module A: PyTorch Profiler  ← Uses CUPTI ✗ Conflict
  DCGM_FI_PROF_TENSOR_ACTIVE ←┤     Module B: Nsight Systems    ← Uses CUPTI ✗ Conflict
  DCGM_FI_PROF_DRAM_ACTIVE   ←┤     Module C: CUPTI Wrapper     ← Direct CUPTI ✗ Conflict
  (internally uses CUPTI)    ←┘

  CUPTI Activity API: "limited to 1~2 concurrent subscribers"
  → If L2 DCGM Profiling is occupying CUPTI
  → L3 tools will get errors or inaccurate results when additionally using CUPTI

  Note: This conflict does NOT affect L1 basic metrics (GPU Util, Temp, etc.)
        L1 uses the NVML API and is unrelated to CUPTI
```

**Conflicting and safe combinations:**

| L2 Status | L3 Module | Conflict | Description |
|---|---|---|---|
| DCGM Profiling **ON** | Module A (PyTorch) | ✗ Conflict | Both use CUPTI Activity |
| DCGM Profiling **ON** | Module B (Nsight Sys) | ✗ Conflict | Nsight is also CUPTI-based |
| DCGM Profiling **ON** | Module C (CUPTI) | ✗ Conflict | Direct CUPTI contention |
| DCGM Profiling **OFF** (L1 only) | Module A/B/C | ✅ Safe | Exclusive CUPTI usage possible |
| DCGM Profiling **ON** | DCGM Job Stats | ✅ Safe | DCGM internal statistics (separate API) |

#### 3.5.2 Recommended Strategy: L2 Pause Protocol

When running L3 profiling, **temporarily pause L2 DCGM Profiling only for the target GPU**, then resume after profiling completes.

```
L3 Profiling Execution Flow (managed by Profiling Controller):

  Normal:  L1 ✅  +  L2 Profiling ✅  (CUPTI: in use by DCGM)
  ────────────────────────────────────────────────────────────

  ① L3 trigger occurs (manual/vmalert/schedule)
     Profiling Controller receives request

  ② Pause DCGM Profiling (target GPU only)
     Profiling Controller → DCGM API call:
       dcgmi profile --pause -g <gpu_group>
     Or remove PROF_* from DCGM Exporter counter CSV and reload

     At this point: L1 ✅  +  L2 Profiling ⏸  (CUPTI: released)
     → Short gap appears in L2 Grafana dashboard (10 seconds ~ few minutes)
     → L1 metrics (GPU Util, Temp, etc.) continue collecting without interruption

  ③ Execute L3 profiling
     Run selected module among A/B/C (exclusive CUPTI usage)
     → 10~60 second profiling session

  ④ L3 profiling complete
     Collect results → Result Processor → Load into ClickHouse

  ⑤ Resume DCGM Profiling
     Profiling Controller → DCGM API call:
       dcgmi profile --resume -g <gpu_group>

     At this point: L1 ✅  +  L2 Profiling ✅  (CUPTI: back in use by DCGM)

  Total L2 downtime: profiling session length + transition overhead (~few seconds)
  → Typically a 30-second ~ 2-minute L2 gap (L1 unaffected)
```

#### 3.5.3 Alternative Strategy: DCGM Job Stats (No Conflict)

To completely avoid CUPTI conflicts, you can use DCGM's built-in **Job Statistics feature**. DCGM Job Stats uses DCGM internal counters rather than CUPTI, so it does not conflict with L2 Profiling.

```
DCGM Job Stats approach:

  Instruct DCGM to "collect statistics for this GPU group" → Receive aggregated results after the period ends

  Job start:
    dcgmi stats -s <job_id> -g <gpu_group>   # Start statistics collection

  During Job execution:
    DCGM internally aggregates GPU metrics (can run in parallel with L2 Profiling!)

  Job end:
    dcgmi stats -x <job_id> -v               # Stop statistics collection + output results
    → GPU Utilization (avg/max)
    → Memory Utilization (avg/max)
    → SM Clock Throttling count
    → ECC error count
    → Power Usage (avg/max)
    → PCIe Throughput

  Advantage: Can run simultaneously with L2 Profiling, no CUPTI conflict
  Disadvantage: No kernel-level analysis (cannot see kernel names, execution times, etc.)
        → More suited for "Job-level efficiency summary" than L3's "deep analysis"
```

#### 3.5.4 Strategy Comparison and Recommendation

| Strategy | L2 Interruption | Kernel Analysis | Implementation Complexity | Recommended Timing |
|---|---|---|---|---|
| **A: L2 pause + L3** | ⏸ Short gap | ✅ Possible | Medium | Phase 5 (when building L3 system) |
| **B: DCGM Job Stats** | None | ❌ Not possible | Low | Phase 3 (alongside IBS integration) |
| **C: L3 dedicated nodes** | None | ✅ Possible | Low | When spare GPU nodes are available |

**Recommended combination:**

```
Phase 3 (immediate):
  • Collect per-IBS-Job GPU efficiency summaries with DCGM Job Stats
  • Obtain Job-level statistics without conflicting with L2 Profiling
  • Integrate with IBS Job lifecycle hooks for DCGM stats start/stop

Phase 5 (L3 system build):
  • Implement L2 pause protocol
  • Profiling Controller manages DCGM Profiling pause/resume
  • Primarily operate Module A (PyTorch) or Module B (Nsight)
  • Deprioritize Module C (CUPTI Wrapper)
    → Nsight Systems provides richer data while being non-invasive

Phase 6 (enhancement):
  • Designate dedicated profiling nodes (L2 Profiling OFF, L3 always available)
  • Or re-evaluate after confirming CUPTI concurrency improvements in CUDA 12.6+
```

#### 3.5.5 Module Comparison Table (Reflecting CUPTI Conflicts)

| | Module A: PyTorch Profiler | Module B: Nsight Systems | DCGM Job Stats |
|---|---|---|---|
| **Primary target** | Training | Training + Inference (general) | Training + Inference (Job summary) |
| **Activation method** | Environment variable (in code) | CLI attach (non-invasive) | DCGM API (non-invasive) |
| **Code modification required** | ✅ (env var branching) | ❌ | ❌ |
| **Overhead** | 5~10% | 5~20% | ~0% |
| **Data richness** | Operator-level | Full timeline | Job-level aggregation only |
| **CUPTI conflict** | ✗ L2 pause required | ✗ L2 pause required | ✅ No conflict |
| **Simultaneous with L2** | ❌ | ❌ | ✅ |
| **Kernel name identification** | ✅ | ✅ | ❌ |
| **Recommended Phase** | Phase 5 | Phase 5 | **Phase 3** |

> **Note**: The existing Module C (CUPTI Wrapper) is deprioritized. Module B (Nsight Systems) is non-invasive yet provides richer timeline data than a CUPTI Wrapper, and since it uses CUPTI in the same way, there is less reason to develop a separate CUPTI Wrapper. For inference environments, Nsight Systems `--duration=30` attach is also more practical.

Triggers: Manual REST API, vmalert auto-trigger, CronJob schedule

### 3.6 Training + Inference Unified Metric Classification Summary

| Category | Metrics | Training | Inference | Collection Source | Storage | Collection Method |
|---|---|---|---|---|---|---|
| **L1 GPU HW** | Util, Temp, Power, Xid | ✅ | ✅ | DCGM basic (NVML) | VictoriaMetrics | Always-on |
| **L2 GPU Efficiency** | SM Active, Tensor Active, DRAM | ✅ | ✅ | DCGM Profiling (CUPTI) | VictoriaMetrics | Always-on ※ Paused during L3 |
| **L2 NVLink/PCIe** | TX/RX Bytes | ✅ | ✅ | DCGM Profiling (CUPTI) | VictoriaMetrics | Always-on ※ Paused during L3 |
| **L2 Inference Latency** | TTFT, TPOT, ITL, E2E | - | ✅ | Inference server /metrics | VictoriaMetrics | Always-on |
| **L2 Inference Throughput** | Tokens/sec, Requests/sec | - | ✅ | Inference server /metrics | VictoriaMetrics | Always-on |
| **L2 KV Cache** | Utilization, Hit Rate, Eviction | - | ✅ | Inference server /metrics | VictoriaMetrics | Always-on |
| **L2 Batching** | Batch Size, Queue Length | - | ✅ | Inference server /metrics | VictoriaMetrics | Always-on |
| **L2 NCCL Communication** | AllReduce/AllGather time | ✅ | - | NCCL logs → Vector | ClickHouse | Always-on (logs) |
| **L2.5 Job Statistics** | Per-Job GPU Util/Mem aggregation | ✅ | ✅ | **DCGM Job Stats** | ClickHouse | **IBS integration (no conflict)** |
| **L3 Kernel Analysis** | Kernel time, Memory patterns | ✅ | ✅ | Module A/B | ClickHouse | **On-demand (L2 paused)** |

---

## 4. Legacy System Metadata Integration (v4 New)

### 4.1 Why Is Legacy Metadata Needed?

If the GPU monitoring system only tells us "GPU 0 is busy," it provides no practical help to operators. We need to know **who is using it, with what Job, and from which VM**.

```
Current: Only GPU metrics collected (DCGM)
──────────────────────────
  DCGM_FI_DEV_GPU_UTIL{node="gpu-node-03", gpu="0"} = 92%

  → "GPU 0 on gpu-node-03 is 92% busy"
  → So what should we do?
  → Who is using it? What Job? How much longer?

v4: GPU metrics + legacy metadata combination
───────────────────────────────────────
  GPU Util 92% + IBS Job #84723
    → User: User A (Team A)
    → Job: llama-70b-finetune
    → Queue: high-priority
    → GPU allocation: gpu-node-03 GPU 0~3
    → Submit time: 2025-07-15 09:00
    → Estimated completion: 2025-07-15 21:00

  GPU Util 45% + VMware VM Inventory
    → VM: vm-gpu-research-07
    → ESXi Host: esxi-gpu-02.internal
    → GPU: A100 (Passthrough)
    → Resource Pool: pool-a
    → Contact: User B
```

### 4.2 Target Legacy Systems

#### 4.2.1 Internal Batch Scheduler (IBS)

IBS stands for Internal Batch Scheduler. It performs a role similar to Slurm, managing Job submission, scheduling, execution, and completion.

**Metadata to collect (4 data types, 3 storage strategies):**

| Data | Description | Storage Strategy | Usage |
|---|---|---|---|
| **Job information** | Job ID, name, user, team, queue, status, submit/start/end time, allocated nodes/GPUs | **Time-series** (MergeTree) | GPU metrics combination, wait time analysis, GPU-Hours calculation |
| **Node status** | Node name, status (idle/alloc/drain/down), partition, GPU allocation status | **Current value** (ReplacingMT) | Cluster capacity/availability, immediate identification of failed nodes |
| **Project information** | FairShare weight, resource Limit, License, allowed queues/Pools | **Snapshot** (ReplacingMT, JSON) | FairShare vs. actual usage rate, resource limit management |
| **Pool information** | Logical Node Pool configuration, member node list, GPU configuration, scheduling policy | **Snapshot** (ReplacingMT, JSON) | Per-Pool GPU status, Node↔Pool mapping |

**IBS Data Access Methods (priority order):**

```
Method 1: IBS REST API Polling (recommended)
  If IBS provides a REST API, Metadata Collector periodically calls the API.

  Expected endpoints (adjust according to IBS API spec):
    GET /api/v1/jobs?status=running,pending,completed
    GET /api/v1/nodes
    GET /api/v1/projects
    GET /api/v1/pools

Method 2: IBS CLI Parsing
  Parse the output of IBS CLI (ibsjobs, ibsnodes, etc.) into structured data.
  Metadata Collector runs CLI via SSH or locally.

  Example: ibsjobs --format=json --state=running
      ibsnodes --format=json
      ibsprojects --format=json
      ibspools --format=json

Method 3: IBS DB Direct Query (fallback)
  Read-only access to the IBS backend DB for querying.
  High dependency on DB schema, maintenance burden.
```

#### 4.2.2 VMware vCenter (GPU VM Inventory)

In VMware vSphere environments, GPUs are assigned to VMs via GPU Passthrough or vGPU. VM↔GPU↔ESXi Host mapping information is collected through the vCenter API.

**Metadata to collect:**

| Data | Description | Usage |
|---|---|---|
| **VM Inventory** | VM name, UUID, status, creation date, contact (annotation) | GPU VM status overview, inventory management |
| **VM↔Host Mapping** | Which ESXi host the VM is running on | Identify affected VMs during host failures |
| **GPU Allocation** | GPU type assigned to VM (Passthrough/vGPU), profile | Per-VM GPU resource tracking |
| **Resource Pool** | Resource Pool / Cluster the VM belongs to | GPU usage aggregation by team/project |
| **Performance Snapshot** | VM CPU/Memory utilization (vCenter statistics) | Host resource utilization check |

**vCenter Data Access Method:**

```
Method: vCenter REST API (pyVmomi or govmomi)
  vCenter 7.x+ provides a REST API. Access via pyVmomi (Python SDK).

  Collection flow:
    1. Connect to ServiceInstance (vCenter IP + service account)
    2. Query VirtualMachine object list via content.viewManager
    3. Extract metadata from each VM's config, runtime, guest properties
    4. Filter GPU PCI devices from config.hardware.device
    5. Extract ESXi host information from runtime.host

  Authentication: Service account (read-only permissions sufficient)
  Protocol: HTTPS (vCenter port 443)
```

#### 4.2.3 Other Extensible Sources (Future)

| Source | Data | Notes |
|---|---|---|
| **LDAP/AD** | User→Department/Team mapping | Extend IBS user IDs to organizational information |
| **CMDB** | Asset information (serial, rack, IDC location) | Integrate with data managed in Zabbix |
| **K8s API** | Namespace, Pod, ResourceQuota | Already collected by kube-state-metrics |

### 4.3 Metadata Collector Design

Metadata Collector is a **single component** that periodically polls legacy system APIs and loads data into ClickHouse with a standard schema.

#### 4.3.1 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Metadata Collector                           │
│                  (K8s Deployment, 1 replica)                     │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      Scheduler                             │  │
│  │  IBS Jobs:       every 60s (running/pending) — time-series  │  │
│  │  IBS Jobs:       every 300s (recently_completed) — time-series│ │
│  │  IBS Nodes:      every 120s — current value (ReplacingMT)   │  │
│  │  IBS Projects:   every 600s — snapshot (rarely changes)      │  │
│  │  IBS Pools:      every 600s — snapshot (rarely changes)      │  │
│  │  VMware VMs:    every 300s — current value (ReplacingMT)    │  │
│  └────────┬──────────┬──────────┬──────────┬─────────────────┘  │
│           │          │          │          │                      │
│           ▼          ▼          ▼          ▼                      │
│  ┌─────────────┐ ┌────────┐ ┌────────────┐ ┌────────────────┐  │
│  │ IBS Adapter  │ │IBS Adap.│ │ IBS Adapter │ │ VMware Adapter │  │
│  │ (Jobs)      │ │(Nodes) │ │ (Projects  │ │ (VMs)          │  │
│  │             │ │        │ │  + Pools)   │ │                │  │
│  │ REST/CLI    │ │REST/CLI│ │ REST/CLI    │ │ pyVmomi        │  │
│  └─────┬───────┘ └───┬────┘ └─────┬──────┘ └───────┬────────┘  │
│        │             │            │                  │            │
│        └─────────────┼────────────┼──────────────────┘            │
│                           ▼                                      │
│              ┌─────────────────────┐                             │
│              │  Schema Normalizer  │                             │
│              │  Standard schema    │                             │
│              │  transformation     │                             │
│              └──────────┬──────────┘                             │
│                         │                                        │
│                         ▼                                        │
│              ┌─────────────────────┐                             │
│              │  ClickHouse Writer  │                             │
│              │  Batch INSERT       │                             │
│              └─────────────────────┘                             │
│                                                                  │
│  Config:                                                         │
│    /etc/metadata-collector/config.yaml                           │
│    or K8s ConfigMap + Secret (vCenter credentials, etc.)         │
└─────────────────────────────────────────────────────────────────┘
```

#### 4.3.2 Configuration File Structure (config.yaml)

```yaml
# Metadata Collector Configuration
collector:
  log_level: info
  health_port: 8080

# ClickHouse connection
clickhouse:
  endpoints:
    - clickhouse-cluster.clickhouse.svc:9000
  database: gpu_monitoring
  username: metadata_writer
  password_secret: metadata-collector-secrets  # K8s Secret reference
  batch_size: 500
  flush_interval: 10s

# IBS scheduler connection
sources:
  ibs:
    enabled: true
    # Method 1: REST API
    api_url: "http://s2-master.internal:8080/api/v1"
    auth_token_secret: ibs-api-token
    # Method 2: CLI (when API is unavailable)
    # cli_mode: true
    # cli_path: /usr/local/bin/ibsjobs
    # ssh_host: s2-master.internal
    # ssh_user: monitor
    # ssh_key_secret: ibs-ssh-key

    schedules:
      jobs_running:
        interval: 60s
        storage: timeseries           # MergeTree — time-series history retention
        filters: { status: [running, pending] }
      jobs_completed:
        interval: 300s
        storage: timeseries
        filters: { status: [completed, failed, cancelled], since: "24h" }
      nodes:
        interval: 120s
        storage: current_value        # ReplacingMergeTree — latest state only
      projects:
        interval: 600s
        storage: snapshot             # ReplacingMergeTree — update on change
      pools:
        interval: 600s
        storage: snapshot

  vmware:
    enabled: true
    vcenter_url: "https://vcenter.internal.example.com"
    username_secret: vcenter-credentials  # K8s Secret reference
    insecure_skip_verify: false

    schedules:
      vm_inventory:
        interval: 300s
        # Filter only VMs with GPU assigned
        filter: "config.hardware.device HAS VirtualPCIPassthrough OR config.hardware.device HAS SharedPassthroughVgpu"
```

#### 4.3.3 Implementation Language and Technology Stack

| Item | Choice | Reason |
|---|---|---|
| **Language** | Python (FastAPI) or Go | Python: easy to use pyVmomi (VMware SDK), rapid prototyping. Go: long-term performance |
| **VMware SDK** | pyVmomi | Official VMware Python SDK. Supports vCenter 6.5+ |
| **IBS integration** | HTTP client / subprocess | API method or CLI output parsing |
| **ClickHouse client** | clickhouse-driver (Python) / clickhouse-go | Supports Batch INSERT |
| **Scheduling** | APScheduler (Python) or built-in ticker (Go) | Independent per-source interval management |
| **Container image** | Alpine/Distroless based | Lightweight image, push to a container registry |

#### 4.3.4 Adapter Pattern (Extensibility)

An **Adapter interface** is defined to easily add new metadata sources.

```python
# adapter.py (conceptual code)
from abc import ABC, abstractmethod
from typing import List, Dict

class MetadataAdapter(ABC):
    """Base class for all metadata source Adapters"""

    @abstractmethod
    def fetch(self) -> List[Dict]:
        """Fetch metadata from source and return as standardized dict list"""
        pass

    @abstractmethod
    def get_table_name(self) -> str:
        """Return ClickHouse target table name"""
        pass

class IBSJobsAdapter(MetadataAdapter):
    """IBS scheduler Job metadata collection"""

    def fetch(self) -> List[Dict]:
        # REST API call or CLI execution
        response = self.client.get("/api/v1/jobs", params=self.filters)
        return [self._normalize(job) for job in response.json()["jobs"]]

    def _normalize(self, raw_job: Dict) -> Dict:
        return {
            "collected_at": datetime.utcnow(),
            "job_id": str(raw_job["id"]),
            "job_name": raw_job.get("name", ""),
            "user_id": raw_job["user"],
            "team": raw_job.get("group", ""),
            "queue": raw_job.get("partition", "default"),
            "status": raw_job["state"],
            "submit_time": parse_time(raw_job.get("submit_time")),
            "start_time": parse_time(raw_job.get("start_time")),
            "end_time": parse_time(raw_job.get("end_time")),
            "node_list": raw_job.get("nodes", []),
            "gpu_count": raw_job.get("gpu_count", 0),
            "gpu_indices": raw_job.get("gpu_indices", []),
            "cpu_count": raw_job.get("cpu_count", 0),
            "memory_mb": raw_job.get("memory_mb", 0),
            "exit_code": raw_job.get("exit_code"),
            "metadata": json.dumps(raw_job.get("extra", {})),
        }

class VMwareVMAdapter(MetadataAdapter):
    """VMware vCenter GPU VM Inventory collection"""

    def fetch(self) -> List[Dict]:
        # Query VM list via pyVmomi
        content = self.si.RetrieveContent()
        vms = self._get_all_vms(content)
        return [self._normalize(vm) for vm in vms if self._has_gpu(vm)]

    def _normalize(self, vm) -> Dict:
        gpu_devices = self._extract_gpu_devices(vm)
        return {
            "collected_at": datetime.utcnow(),
            "vm_name": vm.name,
            "vm_uuid": vm.config.uuid,
            "vm_status": str(vm.runtime.powerState),
            "esxi_host": vm.runtime.host.name,
            "cluster": vm.runtime.host.parent.name,
            "resource_pool": vm.resourcePool.name if vm.resourcePool else "",
            "guest_os": vm.config.guestFullName or "",
            "vcpu_count": vm.config.hardware.numCPU,
            "memory_mb": vm.config.hardware.memoryMB,
            "gpu_count": len(gpu_devices),
            "gpu_type": gpu_devices[0]["type"] if gpu_devices else "",
            "gpu_profile": gpu_devices[0].get("profile", "") if gpu_devices else "",
            "gpu_pci_ids": json.dumps([g["pci_id"] for g in gpu_devices]),
            "annotation": vm.config.annotation or "",
            "metadata": json.dumps({
                "datacenter": self._get_datacenter(vm),
                "folder": vm.parent.name if vm.parent else "",
                "create_date": str(vm.config.createDate) if hasattr(vm.config, 'createDate') else "",
            }),
        }
```

### 4.4 Combining Metadata with GPU Metrics (Enrichment)

The true value of collected metadata emerges when it is **combined with GPU metrics**.

#### 4.4.1 Combination Methods

```
Method 1: Grafana Query-Time JOIN (Recommended, Phase 3)
─────────────────────────────────────────────
  Combine two data sources in the Grafana dashboard.

  Panel A (VictoriaMetrics):
    DCGM_FI_DEV_GPU_UTIL{node="gpu-node-03", gpu="0"}

  Panel B (ClickHouse):
    SELECT job_id, job_name, user_id, team
    FROM ibs_jobs
    WHERE has(node_list, 'gpu-node-03')
      AND has(gpu_indices, 0)
      AND status = 'running'
    ORDER BY collected_at DESC LIMIT 1

  Result: Currently running Job info displayed above the GPU Util graph

Method 2: Merge via Grafana Transformations
──────────────────────────────────────────
  Display two queries merged as a table within the same panel:

  Query A: VictoriaMetrics → GPU Util by (node, gpu)
  Query B: ClickHouse → ibs_jobs (running) by (node_list, gpu_indices)
  Transform: Merge → Join based on node

  Result: | node | gpu | GPU Util | Job ID | User | Team | table

Method 3: Metric Enrichment via vmagent (Phase 6 Advanced)
──────────────────────────────────────────────────────
  Metadata Collector exposes mapping info in Prometheus format:

    gpu_job_mapping{node="gpu-node-03", gpu="0", job_id="84723",
                    user="kim", team="ai-research"} = 1

  vmagent reads this mapping and adds labels to DCGM metrics.
  → Can filter by job_id directly in PromQL.
  → High implementation complexity (metric_relabel + recording rules)
```

#### 4.4.2 Usage Scenarios

**Scenario 1: "Identify Jobs with Low GPU Util"**

```sql
-- ClickHouse: Currently running Jobs and GPU allocation info
SELECT j.job_id, j.job_name, j.user_id, j.team, j.node_list, j.gpu_count
FROM ibs_jobs j
WHERE j.status = 'running'
  AND j.collected_at = (SELECT max(collected_at) FROM ibs_jobs WHERE job_id = j.job_id)
```

```promql
-- VictoriaMetrics: GPU Util for the corresponding node
avg_over_time(DCGM_FI_DEV_GPU_UTIL{node=~"$node"}[5m])
```

→ Can calculate **average GPU Util per Job** in the dashboard

**Scenario 2: "GPU Usage Status for a Specific Team"**

```sql
-- ClickHouse: All running Jobs for a specific team
SELECT j.job_id, j.node_list, j.gpu_count,
       j.gpu_indices, j.submit_time, j.start_time
FROM ibs_jobs j
WHERE j.team = 'ai-research' AND j.status = 'running'
  AND j.collected_at = (SELECT max(collected_at) FROM ibs_jobs WHERE job_id = j.job_id)
```

→ Generate reports on GPU occupancy rate, wait time, and efficiency per team

**Scenario 3: "VMware GPU VM Inventory Status"**

```sql
-- ClickHouse: Latest snapshot of all VMs with GPUs assigned
SELECT vm_name, esxi_host, gpu_type, gpu_count,
       resource_pool, vm_status, vcpu_count, memory_mb
FROM vmware_vm_inventory
WHERE collected_at = (SELECT max(collected_at) FROM vmware_vm_inventory WHERE vm_uuid = v.vm_uuid)
ORDER BY resource_pool, vm_name
```

→ View GPU VM allocation status per team, GPU distribution per ESXi host

**Scenario 4: "Impact Scope When an ESXi Host Fails"**

```sql
-- GPU VMs running on a specific ESXi host
SELECT vm_name, gpu_type, gpu_count, resource_pool, annotation
FROM vmware_vm_inventory
WHERE esxi_host = 'esxi-gpu-02.internal'
  AND vm_status = 'poweredOn'
  AND collected_at >= now() - INTERVAL 10 MINUTE
```

→ Immediately identify affected VMs and responsible teams when a host fails

### 4.5 Data Retention and History Management (v4.1 Revised)

```
IBS Job Data [Time-series, MergeTree]:
  ├── Running/Pending Job: Every 60 seconds → Full time-series history retained
  ├── Completed Job: Additional record after completion
  └── Retention: 6 months (TTL) — Used for Job wait time, GPU-Hours analysis

IBS Node Data [Current value, ReplacingMergeTree]:
  ├── Polled every 120 seconds → Only the latest 1 record per node retained
  ├── If history is needed, track indirectly via ibs_jobs or record events in gpu_events
  └── No TTL (row count remains stable due to automatic dedup)

IBS Project/Pool Data [Snapshot, ReplacingMergeTree]:
  ├── Polled every 600 seconds → Effectively updated only on changes
  ├── JSON field-centric (FairShare, Limit, License, Node configuration, etc.)
  └── No TTL (change history automatically maintains only the latest)

VMware VM Inventory [Current value, ReplacingMergeTree]:
  ├── GPU VM list every 300 seconds → Latest 1 record per VM
  └── Retention: 12 months (TTL)

History analysis examples:
  "Change in GPU usage patterns of Team A over the last 3 months" → ibs_jobs time-series
  "How long was the wait time for a specific Job" → ibs_jobs pending→running transition
  "History of a VM being migrated to a different ESXi host" → vmware_vm_inventory
```

### 4.6 Per-Job GPU Utilization Measurement Strategy (v4.1 New)

Measuring "GPU Utilization of a specific Job" in GPU environments is fundamentally different from CPU's `perf attach`. Understanding this difference enables proper collection strategies.

#### 4.6.1 CPU vs GPU: Differences in Per-Process Monitoring

```
CPU World:                                GPU World:
─────────                                 ─────────
• CPU cores are time-shared across        • GPUs are typically allocated "entirely"
  processes by the OS scheduler             to a Job exclusively (Exclusive Mode)
• Process A and B take turns running      • GPU 0 = Job A only, GPU 1 = Job B only
  on the same core                        • → If a Job has exclusive GPU access,
• → To distinguish per-process CPU          GPU metrics = Job metrics
  usage, PMU counter measurement like
  perf attach is essential               • Per-process measurement needed only
                                            when GPU sharing (MPS/MIG)

Key Insight:
  In HPC/AI environments, exclusive GPU allocation to Jobs is standard
  → If IBS tells us "Job #84723 → GPU [0,1,2,3] on gpu-node-03"
  → DCGM metrics for gpu-node-03 GPU 0~3 are exactly Job #84723's metrics
  → No profiler attach needed!
```

#### 4.6.2 Measurement Methods by Scenario

**Scenario 1: Exclusive GPU Allocation (Most HPC/AI Environments) — Indirect Combination**

```
This is the method adopted by most GPU cluster environments.
Since IBS assigns GPUs exclusively to each Job, per-GPU metrics = per-Job metrics.

Measurement Flow:
  ┌───────────────┐     ┌──────────────────────┐     ┌─────────────┐
  │ IBS Metadata   │     │ DCGM Exporter        │     │ Grafana     │
  │               │     │                      │     │             │
  │ Job #84723    │     │ gpu-node-03:         │     │ JOIN:       │
  │  node: gpu-03 │ ──→ │  GPU 0: Util 92%    │ ──→ │ Job #84723  │
  │  gpus: [0,1,  │     │  GPU 1: Util 88%    │     │ Avg GPU Util│
  │         2,3]  │     │  GPU 2: Util 91%    │     │ = 90.5%     │
  │               │     │  GPU 3: Util 90%    │     │             │
  └───────────────┘     └──────────────────────┘     └─────────────┘
    (ClickHouse)           (VictoriaMetrics)           (Query-time JOIN)

Implementation (Grafana Query):
  1. Query Job's (node, gpu_indices) from ClickHouse
  2. Query corresponding node+gpu DCGM metrics from VictoriaMetrics
  3. Merge via Transformations → Display GPU Util per Job

Advantages: No additional agent/profiler installation needed (uses already-collected data)
Limitations: Cannot distinguish per-Job in GPU-shared environments
```

**Scenario 2: GPU Shared Environment (MPS / Time-sharing) — Per-Process Measurement Needed**

```
This is a rare case where multiple processes share the same GPU.
In this case, NVIDIA's per-process measurement capabilities are used.

Method A: NVML Accounting Mode (Recommended, ~0% overhead)
  ────────────────────────────────────────────────
  $ nvidia-smi -am 1  # Enable Accounting Mode (one-time)

  Query per-PID GPU usage statistics via NVML API:
    nvmlDeviceGetAccountingStats(device, pid)
    → gpu_utilization: Percentage of time the PID executed GPU kernels
    → memory_utilization: Percentage of FB memory access by PID
    → max_memory_usage: Maximum GPU memory usage by PID

  Expose as Prometheus metrics via custom Exporter:
    gpu_process_util{pid="12345", gpu="0", node="gpu-03"} = 45.2
    gpu_process_memory{pid="12345", gpu="0", node="gpu-03"} = 8192

  Map with IBS Job PID info:
    If ibs_jobs.metadata JSON contains {"pid": 12345}
    → JOIN gpu_process_util and ibs_jobs by PID

Method B: DCGM Job Stats API (Scheduler Integration)
  ────────────────────────────────────────
  This is a built-in Job-level statistics feature in DCGM.
  When IBS notifies DCGM at Job start/end, DCGM aggregates
  GPU usage statistics for that period.

  Integration flow:
    Job start → IBS Hook → dcgmi stats -s <group_id>  (Start stats collection)
    Job end   → IBS Hook → dcgmi stats -x <group_id>  (Stop stats collection)
    → Result: GPU Util, Memory, ECC errors, SM Occupancy, etc. for the Job period

  Advantages: Provides comprehensive stats including SM Clock, Memory, not just GPU Util
  Disadvantages: Requires IBS Job lifecycle hooks (coordination with scheduler operators needed)

Method C: NVIDIA MIG (A100/H100 Only)
  ────────────────────────────────────
  Physically partition GPU into up to 7 instances.
  Each MIG instance operates as an independent GPU.
  → DCGM automatically exposes metrics per MIG instance.
  → Cleanest approach but limited concurrent instances per GPU.
```

#### 4.6.3 Recommended Implementation Roadmap

```
Phase 3 (Immediate Implementation): Indirect Combination Method
─────────────────────────────────────
  • JOIN IBS Job metadata (node_list, gpu_indices) with
    DCGM per-GPU metrics in Grafana
  • This alone enables per-Job GPU Util verification in exclusive GPU allocation environments
  • No additional agent installation required
  • → Implement per-Job GPU Util panel in the "Job Explorer" dashboard

Phase 6 (Advanced, As Needed):
─────────────────────────
  Option A: NVML Accounting Exporter (GPU sharing environment support)
    • Develop custom Prometheus Exporter (Python/Go)
    • Per-PID GPU Utilization → Expose as Prometheus metrics
    • vmagent scrapes → VictoriaMetrics → Grafana
    • Map with IBS Job PID

  Option B: DCGM Job Stats Integration (Requires IBS Hook)
    • Signal DCGM to start/stop stats collection at IBS Job start/end
    • Load aggregated results to ClickHouse upon completion
    • For post-hoc analysis (reporting purposes rather than real-time monitoring)
```

#### 4.6.4 Per-Job GPU Util Data Flow (Phase 3)

```
┌──────────────────┐        ┌───────────────────┐        ┌─────────────┐
│   IBS Scheduler   │        │  GPU Node          │        │   Grafana   │
│                  │        │                    │        │             │
│ Job #84723       │        │ DCGM Exporter      │        │ Query A:    │
│  node: gpu-03    │  ──→   │  GPU 0: Util 92%   │  ──→   │ ClickHouse  │
│  gpus: [0,1,2,3] │ (IBS    │  GPU 1: Util 88%   │ (DCGM  │ → Job info  │
│  user: Kim OO    │  meta)  │  GPU 2: Util 91%   │  →VM)  │             │
│  team: AI Research│        │  GPU 3: Util 90%   │        │ Query B:    │
│                  │        │                    │        │ VictoriaM.  │
│ → ClickHouse     │        │ → VictoriaMetrics  │        │ → GPU Util  │
│   (ibs_jobs)      │        │                    │        │             │
└──────────────────┘        └───────────────────┘        │ Transform:  │
                                                          │ Merge on    │
                                                          │ node + gpu  │
                                                          │             │
                                                          │ Result:     │
                                                          │ Job #84723  │
                                                          │ Avg GPU Util│
                                                          │ = 90.25%    │
                                                          └─────────────┘
```

---

## 5. Overall Architecture Overview (v5)

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                              DATA SOURCES                                      │
│     ※ Only Exporter + lightweight Vector Agent deployed on nodes (no vmagent)│
│                                                                                │
│  ┌──────────────────────────┐ ┌────────────────────┐ ┌─────────────────────┐ │
│  │ Baremetal GPU Clusters    │ │ K8s GPU Clusters   │ │ GPU VMs (VMware)    │ │
│  │ (IBS Batch Scheduler)     │ │                    │ │ (Managed by vCenter)│ │
│  │                           │ │                    │ │                      │ │
│  │ [Node - systemd deploy]  │ │ [Node - DaemonSet] │ │ [VM - systemd deploy]│ │
│  │ • DCGM Exporter (:9400)  │ │ • DCGM Exporter    │ │ • DCGM Exporter     │ │
│  │ • node_exporter (:9100)  │ │ • node_exporter    │ │ • node_exporter     │ │
│  │ • Vector Agent (lightweight)│ │ • Vector Agent   │ │ • Vector Agent      │ │
│  │   (log forward only)     │ │ • kube-state-m     │ │   (log forward only)│ │
│  │ • Inference server /metrics│ │ • Inference /metrics│ │ • Inference /metrics│ │
│  │                           │ │                    │ │                      │ │
│  │ [Legacy Systems]          │ │                    │ │ [Legacy Systems]     │ │
│  │ • IBS Scheduler            │ │                    │ │ • VMware vCenter     │ │
│  │   ├ Job (time-series)     │ │                    │ │   └ GPU VM Inventory │ │
│  │   ├ Node (current value)  │ │                    │ │                      │ │
│  │   ├ Project (snapshot)    │ │                    │ │                      │ │
│  │   └ Pool (snapshot)       │ │                    │ │                      │ │
│  │                           │ │                    │ │                      │ │
│  │ ※ Zabbix: IPMI/HW/SNMP   │ │                    │ │                      │ │
│  └───────────────────────────┘ └────────────────────┘ └─────────────────────┘ │
│       ↑ Pull (:9400/:9100)       ↑ Pull (K8s SD)       ↑ Pull (:9400/:9100)  │
│       │                          │                       │                     │
│       │    Log Push (vector)     │    Log Push           │    Log Push         │
│       │    ↓                     │    ↓                  │    ↓                │
└───────┼────┼─────────────────────┼────┼──────────────────┼────┼───────────────┘
        │    │                     │    │                   │    │
        │    │                     │    │                   │    │
┌───────┼────┼─────────────────────┼────┼───────────────────┼────┼──────────────┐
│       │    │     COLLECTION LAYER (K8s)                   │    │              │
│       │    │                     │    │                   │    │              │
│  ┌────┴────┴─────────────────────┴────┴───────────────────┴──┐ │              │
│  │  vmagent (Central, K8s Deployment HA)                      │ │              │
│  │                                                            │ │              │
│  │  Pull: Directly scrape all node Exporters                 │ │              │
│  │  ├─ File SD: Baremetal/VM nodes (Ansible-managed JSON)    │ │              │
│  │  ├─ K8s SD: K8s Pod/Service auto-discovery                │ │              │
│  │  └─ Static: Inference servers, kube-state-metrics, etc.   │ │              │
│  │                                                            │ │              │
│  │  → remote_write → VictoriaMetrics                         │ │              │
│  └───────────────────────────────────────────────────────────┘ │              │
│                                                                 │              │
│  ┌──────────────────────────────────────────────────────────┐  │              │
│  │  Vector Aggregator (K8s Deployment)                       ←──┘              │
│  │                                                           │                 │
│  │  ← Receive logs from each node's Vector Agent            │                 │
│  │  → Parse / standardize / remap                           │                 │
│  │  → ClickHouse sink (gpu_unified_logs)                    │                 │
│  └──────────────────────────────────────────────────────────┘                  │
│                                                                                │
│  ┌────────────────────────────────┐                                           │
│  │  Metadata Collector             │  ← Polls IBS API + vCenter API           │
│  │  (K8s Deployment, 1 replica)    │  → ClickHouse Batch INSERT              │
│  └──────────────────────────────── ┘                                          │
│                                                                                │
└──────────────────────┬────────────────────────────────────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                         STORAGE LAYER (K8s)                                    │
│                                                                                │
│  ┌─────────────────────────────┐   ┌────────────────────────────────────────┐ │
│  │   VictoriaMetrics           │   │           ClickHouse                    │ │
│  │   (Cluster Mode)            │   │           (Cluster Mode)                │ │
│  │                             │   │                                         │ │
│  │ ← Central vmagent           │   │ ← Vector Aggregator (logs)             │ │
│  │   remote_write              │   │ ← Metadata Collector INSERT            │ │
│  │                             │   │ ← Profiling Controller INSERT          │ │
│  │ • L1: GPU HW (NVML-based)  │   │ ← Custom API INSERT                    │ │
│  │ • L2: GPU Profiling (CUPTI) │   │                                         │ │
│  │   ⚠ Temporarily paused     │   │ • gpu_unified_logs (logs)               │ │
│  │     during L3 execution     │   │ • gpu_demand / gpu_inventory            │ │
│  │ • L2: Inference server      │   │ • gpu_events (Zabbix)                   │ │
│  │   metrics                   │   │ • gpu_profiling_traces / sessions       │ │
│  │ • System/K8s metrics        │   │ • ibs_jobs / ibs_nodes                   │ │
│  │                             │   │ • ibs_projects / ibs_pools               │ │
│  │ Standard labels: env /      │   │ • vmware_vm_inventory                   │ │
│  │ cluster / node / gpu /      │   │                                         │ │
│  │ workload_type               │   │                                         │ │
│  └──────────────┬──────────────┘   └─────────────────┬───────────────────────┘ │
│                 │                                     │                         │
└─────────────────┼─────────────────────────────────────┼─────────────────────────┘
                  │                                     │
                  ▼                                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                   VISUALIZATION & ALERTING LAYER (K8s)                          │
│                                                                                │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                          Grafana                                         │  │
│  │  • VictoriaMetrics datasource (PromQL/MetricsQL)                        │  │
│  │  • ClickHouse datasource (SQL)                                          │  │
│  │  • Dashboards:                                                          │  │
│  │    - GPU Health (L1) / GPU Efficiency (L2) / Inference SLA (L2)        │  │
│  │    - Training Comm (L2) / Profiling Analysis (L3)                      │  │
│  │    - Demand & Capacity / System Overview                               │  │
│  │    - ★ Job Explorer / ★ VM GPU Inventory                              │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                │
│  ┌──────────────────────────┐  ┌───────────────────────────────────────────┐  │
│  │  vmalert + Alertmanager  │  │  L3 Profiling Controller (On-demand)      │  │
│  │  • GPU anomaly/efficiency│  │  • Module A (PyTorch) / B (Nsight)       │  │
│  │    /SLA alerts           │  │  • ⚠ L2 DCGM Profiling pause/resume     │  │
│  │  • Slack/Email alerts    │  │                                           │  │
│  └──────────────────────────┘  └───────────────────────────────────────────┘  │
│  ┌──────────────────────────┐                                                 │
│  │  Custom Ingestion API    │                                                 │
│  └──────────────────────────┘                                                 │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Data Flow Summary (v5 Pull-based):**

```
Path A — Metrics Pull (Central vmagent → Each Node's Exporter)
  Central vmagent periodically scrapes HTTP endpoints on all nodes

  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────┐
  │ GPU Node          │ ←Pull── │ Central vmagent   │ ──write─→│VictoriaMetrics│
  │  :9400 (DCGM)    │         │ (K8s, HA)         │         │              │
  │  :9100 (node_exp) │         │ File SD + K8s SD  │         │              │
  │  :8080 (inference)│         └───────────────────┘         └──────────────┘
  └──────────────────┘
  ※ No vmagent on nodes!

Path A' — Lightweight Log Push (Node Vector Agent → Central Vector Aggregator)
  Node's Vector Agent reads logs and forwards them to the center (no parsing)

  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────┐
  │ GPU Node          │ ──Push─→│ Vector Aggregator │ ──sink──→│ ClickHouse   │
  │  Vector Agent     │         │ (K8s)             │         │              │
  │  (lightweight,    │         │ (parse/transform/ │         │              │
  │   forward only)   │         │  routing)         │         │              │
  └──────────────────┘         └───────────────────┘         └──────────────┘

Path B — Legacy API Polling (via Metadata Collector)
  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────┐
  │ IBS API Server    │ ←Poll── │ Metadata          │ ──INSERT→│ ClickHouse   │
  │ vCenter API      │         │ Collector (K8s)   │         │              │
  └──────────────────┘         └───────────────────┘         └──────────────┘
```

---

## 6. SW Stack Configuration (Confirmed + Recommended)

### 6.1 Confirmed Components

| Component | Role | Deployment Method | Notes |
|---|---|---|---|
| **ClickHouse** | Log/JSON/profiling/metadata storage | K8s: Altinity ClickHouse Operator | Cluster mode recommended |
| **VictoriaMetrics** | Time-series metric storage (L1+L2) | K8s: VictoriaMetrics Operator | Cluster mode |

### 6.2 Recommended Components - Node Deployment (Exporter + Lightweight Agent)

> **v5 Change**: vmagent removed from nodes. Exporters only expose HTTP, Vector only handles lightweight forwarding.

| Component | Role | Baremetal/VM Deployment | K8s Deployment | Notes |
|---|---|---|---|---|
| **DCGM Exporter** | GPU metrics HTTP exposure (L1+L2 Profiling) | systemd service | DaemonSet (GPU nodes) | **Pull standby (:9400)** |
| **node_exporter** | Host OS metrics HTTP exposure | systemd service | DaemonSet | **Pull standby (:9100)** |
| **Vector Agent** | Log collection → Forward to central Aggregator | systemd service | DaemonSet | **Lightweight Push (forward only)** |
| ~~**vmagent**~~ | ~~Metrics scrape → VM send~~ | ❌ Removed | ❌ Removed | **Removed in v5** |

### 6.3 Recommended Components - Central Collection Layer (K8s Deployment, v5 New)

> **v5 Change**: Control of metrics/log collection shifted to central.

| Component | Role | Deployment Method | Notes |
|---|---|---|---|
| **vmagent (Central)** | Pull scrape all node Exporters → VM remote_write | K8s Deployment (HA, 2+ replicas) | **v5 core, File SD + K8s SD** |
| **Vector Aggregator** | Receive logs from node Vector Agents → Parse/transform → ClickHouse | K8s Deployment | **v5 new** |

### 6.4 Recommended Components - Inference/K8s Specific

| Component | Role | Deployment Method | Notes |
|---|---|---|---|
| **Inference server /metrics** | Inference metrics HTTP exposure (vLLM/TGI/Triton) | Inference server native feature | **Central vmagent Pulls** |
| **kube-state-metrics** | K8s object state metrics | Deployment (1 instance) | **Central vmagent Pulls** |

### 6.5 Recommended Components - Serving/Alerting Layer (K8s Deployment)

| Component | Role | Deployment Method | Alternative |
|---|---|---|---|
| **Grafana** | Visualization/dashboards (VM + ClickHouse integrated) | Helm Chart | - |
| **vmalert** | PromQL-based alert rules + L3 trigger | VictoriaMetrics Operator | - |
| **Alertmanager** | Alert routing (Slack/Email/PagerDuty) | Helm Chart | - |

### 6.6 Recommended Components - Metadata Collection (v4~)

| Component | Role | Deployment Method | Notes |
|---|---|---|---|
| **Metadata Collector** | IBS + VMware metadata collection → ClickHouse | K8s Deployment (1 replica) | Adapter pattern, Python/Go |
| ├─ IBS Adapter | IBS Job/Node metadata polling | Built-in Collector module | REST API or CLI method |
| └─ VMware Adapter | vCenter VM Inventory polling | Built-in Collector module | pyVmomi SDK |

### 6.7 Recommended Components - L2.5 Job Statistics + L3 Profiling

| Component | Role | Deployment Method | CUPTI Conflict | Notes |
|---|---|---|---|---|
| **DCGM Job Stats** | Per-Job GPU efficiency aggregation (L2.5) | IBS Hook integration | ✅ None | Phase 3, concurrent with L2 |
| **Profiling Controller** | L3 request management, **L2 pause/resume** | K8s Deployment | - | L2↔L3 CUPTI conflict management |
| **Module A: PyTorch Profiler** | Training operator analysis | Within training code | ⚠ L2 temporarily paused | Phase 5 |
| **Module B: Nsight Systems** | General-purpose GPU timeline analysis | nsys installed on GPU nodes | ⚠ L2 temporarily paused | Phase 5, non-invasive |
| **Result Processor** | Result parsing → ClickHouse | Built into Profiling Controller | - | - |

> **Module C (CUPTI Wrapper) deprioritized**: Module B (Nsight Systems) provides richer data non-invasively while using the same CUPTI, reducing the need for a separate CUPTI Wrapper development. For inference environments, Nsight Systems `--duration=30` attach is more practical.

### 6.8 Optional Components

| Component | Role | When Needed | Notes |
|---|---|---|---|
| **Kafka (Strimzi Operator)** | Message queue/buffer | 5+ GPU clusters | Start without it initially |
| **Custom Ingestion API** | GPU demand JSON collection | When collecting external demand data | Go/Python FastAPI |
| **Zabbix Agent** | IPMI/HW/SNMP (reduced role) | When maintaining Baremetal HW monitoring | GPU collection migrated |

---

## 7. Data Flow Details

### 7.1 L1+L2 Metrics Flow — Pull-based (v5)

```
  Node (Exporter HTTP exposure only, Pull standby)     Central (K8s)
  ─────────────────────────────────────               ─────────────────────────

  [Baremetal/VM]                              ┌─────────────────────────┐
   DCGM Exporter (:9400, L1+L2)  ◄── Pull ──│                         │
   node_exporter (:9100)          ◄── Pull ──│  vmagent (Central, HA)  │
   Inference server /metrics (:8080) ◄── Pull──│                       │
                                              │  File SD:               │
  [K8s]                                       │   baremetal-gpu-*.json  │──→ VictoriaMetrics
   DCGM Exporter (Pod :9400)     ◄── Pull ──│   vm-gpu-*.json         │    Cluster
   node_exporter (Pod :9100)      ◄── Pull ──│                         │      │
   Inference server /metrics (Pod) ◄── Pull──│  K8s SD:                │  ┌───┴───┐
   kube-state-metrics (:8080)    ◄── Pull ──│   auto-discover Pods    │  │Grafana│
                                              └─────────────────────────┘  └───────┘

  ※ No vmagent on nodes — Exporters just open HTTP endpoints and that's it
  ※ Collection interval/targets/labels are managed in one place: central vmagent config
```

### 7.2 Log Flow — Lightweight Push (v5)

```
  Node (Vector Agent, lightweight)               Central (K8s)
  ──────────────────────────                    ─────────────────────────

  [All environments]                              ┌─────────────────────────┐
   syslog, /var/log/nvidia*   ──→ Vector  ─┐    │                         │
  [K8s]                           Agent    │    │  Vector Aggregator      │
   Pod stdout/stderr          ──→ (light- ─┤──→ │                         │──→ ClickHouse
  [Training env]                  weight)  │    │  • Parse / remap        │    (gpu_unified_logs)
   NCCL logs                  ──→ forward  │    │  • Standard schema      │      │
  [Training env]                  only!    │    │    conversion           │  ┌───┴───┐
   Training framework logs    ──→          ─┘    │  • ClickHouse sink      │  │Grafana│
                                                  └─────────────────────────┘  └───────┘

  ※ Node's Vector Agent forwards raw logs to the center without parsing
  ※ Parsing, transformation, and routing are all handled by the central Vector Aggregator
  ※ Why are logs Push-based?
    → Metrics are "current value snapshots" → Pull is natural (always the latest value whenever read)
    → Logs are "event streams" → Push is required (once a log passes, it doesn't come again)
```

### 7.3 Legacy Metadata Flow — Path B (v4.1)

```
  IBS API/CLI Server                           VMware vCenter API
  (Belongs to Baremetal env)                  (Belongs to VM env)
  ┌──────────────────────┐           ┌──────────────────────────┐
  │ IBS Scheduler         │           │ VMware vCenter            │
  │                      │           │                           │
  │ /api/v1/jobs         │           │ pyVmomi:                  │
  │ /api/v1/nodes        │           │   Query VM + GPU + Host   │
  │ /api/v1/projects     │           │                           │
  │ /api/v1/pools        │           │                           │
  └──────────┬───────────┘           └──────────────┬────────────┘
             │                                      │
             │  API Polling (60~600s)                │  API Polling (300s)
             │                                      │
             ▼                                      ▼
       ┌────────────────────────────────────────────────┐
       │           Metadata Collector                     │
       │           (K8s Deployment, 1 replica)            │
       │                                                  │
       │  IBS Adapters:              VMware Adapter:       │
       │   ├ Jobs    (time-series, 60s) └ VMs (current, 300s)│
       │   ├ Nodes   (current, 120s)                      │
       │   ├ Projects (snapshot, 600s)                     │
       │   └ Pools    (snapshot, 600s)                     │
       └──────────────────────┬───────────────────────────┘
                              │ Batch INSERT
                              ▼
                    ClickHouse Cluster
                  ┌────────────────────────┐
                  │ • ibs_jobs     (time-series)│
                  │ • ibs_nodes    (current)    │
                  │ • ibs_projects (snapshot)   │
                  │ • ibs_pools    (snapshot)   │
                  │ • vmware_vm_inventory      │
                  └───────────┬────────────┘
                              │
                     ┌────────┴────────┐
                     │     Grafana     │
                     │  Job Explorer   │
                     │  VM Inventory   │
                     │  GPU↔Job combine│
                     └─────────────────┘
```

### 7.4 L3 Profiling Flow (On-demand, ⚠ L2 Temporarily Paused)

```
  Manual API / vmalert Automatic / CronJob
         │
         ▼
  Profiling Controller
    ① Temporarily pause DCGM Profiling (target GPU, CUPTI conflict prevention)
    ② Execute Module A (PyTorch) or Module B (Nsight)
    ③ Result Processor → ClickHouse (gpu_profiling_traces/sessions)
    ④ Resume DCGM Profiling
```

### 7.5 GPU Demand/JSON Data Flow

```
External System → Custom Ingestion API → ClickHouse (gpu_demand, gpu_inventory)
```

### 7.6 Existing Zabbix Integration Flow (Baremetal Only)

```
Baremetal → Zabbix Agent → Zabbix Server (IPMI/HW/SNMP only)
                              └→ (Optional) Webhook → ClickHouse (gpu_events)
```

---

## 8. ClickHouse Table Design (Standard Schema)

### 8.1 Unified Log Table

```sql
CREATE TABLE gpu_unified_logs (
    timestamp    DateTime64(3),
    env          LowCardinality(String),
    cluster_id   LowCardinality(String),
    node_id      String,
    gpu_id       Nullable(UInt8),
    log_level    LowCardinality(String),
    source       LowCardinality(String),
    message      String,
    metadata     String
) ENGINE = MergeTree()
PARTITION BY (env, toYYYYMM(timestamp))
ORDER BY (env, cluster_id, node_id, timestamp)
TTL timestamp + INTERVAL 6 MONTH;
```

### 8.2 GPU Demand Data Table

```sql
CREATE TABLE gpu_demand (
    timestamp               DateTime64(3),
    env                     LowCardinality(String),
    cluster_id              LowCardinality(String),
    requester               LowCardinality(String),
    gpu_type                LowCardinality(String),
    gpu_count               UInt16,
    priority                LowCardinality(String),
    status                  LowCardinality(String),
    job_metadata            String,
    estimated_duration_hours Float32
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (cluster_id, gpu_type, timestamp);
```

### 8.3 GPU Inventory Table

```sql
CREATE TABLE gpu_inventory (
    updated_at       DateTime64(3),
    env              LowCardinality(String),
    cluster_id       LowCardinality(String),
    node_id          String,
    gpu_index        UInt8,
    gpu_uuid         String,
    gpu_model        LowCardinality(String),
    gpu_memory_mb    UInt32,
    driver_version   LowCardinality(String),
    cuda_version     LowCardinality(String),
    status           LowCardinality(String),
    metadata         String
) ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY (env, cluster_id)
ORDER BY (env, cluster_id, node_id, gpu_index);
```

### 8.4 Zabbix Event Table (Optional)

```sql
CREATE TABLE gpu_events (
    timestamp        DateTime64(3),
    env              LowCardinality(String),
    cluster_id       LowCardinality(String),
    node_id          String,
    event_type       LowCardinality(String),
    severity         LowCardinality(String),
    source           LowCardinality(String),
    message          String,
    metadata         String
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (cluster_id, node_id, timestamp)
TTL timestamp + INTERVAL 1 YEAR;
```

### 8.5 L3 Profiling Traces/Sessions Table

```sql
CREATE TABLE gpu_profiling_traces (
    timestamp        DateTime64(3),
    env              LowCardinality(String),
    cluster_id       LowCardinality(String),
    node_id          String,
    gpu_id           UInt8,
    session_id       String,
    module           LowCardinality(String),
    trigger_type     LowCardinality(String),
    workload_type    LowCardinality(String),
    event_type       LowCardinality(String),
    kernel_name      String,
    duration_us      Float64,
    grid_size        String,
    block_size       String,
    bytes_transferred UInt64,
    memory_kind      LowCardinality(String),
    metadata         String
) ENGINE = MergeTree()
PARTITION BY (workload_type, toYYYYMM(timestamp))
ORDER BY (session_id, timestamp, gpu_id)
TTL timestamp + INTERVAL 3 MONTH;

CREATE TABLE gpu_profiling_sessions (
    session_id       String,
    timestamp        DateTime64(3),
    env              LowCardinality(String),
    cluster_id       LowCardinality(String),
    node_id          String,
    workload_type    LowCardinality(String),
    module           LowCardinality(String),
    trigger_type     LowCardinality(String),
    duration_seconds UInt32,
    total_kernels    UInt32,
    total_gpu_time_ms Float64,
    total_comm_time_ms Float64,
    total_memcpy_time_ms Float64,
    top_kernels      String,
    compute_ratio    Float32,
    metadata         String
) ENGINE = ReplacingMergeTree(timestamp)
ORDER BY (session_id);
```

### 8.6 IBS Metadata Storage Strategy Overview (v4.1 Revised)

IBS-related data is separated into **3 storage strategies based on the nature of the data**.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    IBS Metadata Storage Strategy                       │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │ Time-series      │  │ Current Value   │  │ Snapshot            │ │
│  │ (History)        │  │ (Current)       │  │                     │ │
│  │ MergeTree        │  │ ReplacingMT     │  │ ReplacingMT         │ │
│  │                  │  │                 │  │                      │ │
│  │ ibs_jobs          │  │ ibs_nodes        │  │ ibs_projects          │ │
│  │                  │  │                 │  │ ibs_pools             │ │
│  │ Retains status   │  │ Only the latest │  │ Updated only on      │ │
│  │ history at all   │  │ 1 record per    │  │ changes              │ │
│  │ points in time   │  │ node retained   │  │ Configuration values │ │
│  │                  │  │                 │  │ recorded (JSON-centric)│ │
│  │ "When was this   │  │ "Is this node   │  │ "What is this        │ │
│  │  Job running and │  │  currently idle │  │  project's current   │ │
│  │  when did it end"│  │  or down"       │  │  FairShare"          │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘ │
│                                                                      │
│  Why split this way?                                                 │
│  • Jobs: Time-axis analysis of state changes                         │
│    (pending→running→completed) is important                          │
│    → Track "when did this Job enter queue and how long was the wait" │
│  • Nodes: Only current state matters; history can be tracked         │
│    indirectly via ibs_jobs                                            │
│    → FINAL query quickly retrieves only the latest 1 row per node   │
│  • Projects/Pools: Changes are infrequent; only snapshots at change │
│    points are retained                                               │
│    → Flexible structure representation via JSON, minimal change      │
│      history maintained                                              │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.7 IBS Job Metadata Table — Time-series (v4.1)

**Storage Strategy: Time-series (MergeTree)** — Retains status at all polling points, enabling time-axis analysis of the Job's lifecycle.

```sql
CREATE TABLE ibs_jobs (
    -- Collection timestamp (time axis of the time-series)
    collected_at     DateTime64(3),         -- Metadata Collector polling time

    -- Job identification
    job_id           String,                -- IBS Job unique ID
    job_name         String,                -- Job name (user-specified)

    -- User/organization info
    user_id          LowCardinality(String),  -- Submitter ID
    team             LowCardinality(String),  -- Team/group
    project          LowCardinality(String),  -- IBS Project name (FairShare unit)
    queue            LowCardinality(String),  -- IBS Queue (partition) name

    -- Job status (core field tracked as time-series)
    status           LowCardinality(String),  -- pending, running, completed, failed, cancelled

    -- Time info
    submit_time      Nullable(DateTime64(3)), -- Submission time
    start_time       Nullable(DateTime64(3)), -- Execution start time
    end_time         Nullable(DateTime64(3)), -- Completion time

    -- Resource allocation (core for GPU metric combination)
    node_list        Array(String),           -- ['gpu-node-03', 'gpu-node-04']
    gpu_count        UInt16,                  -- Total allocated GPUs
    gpu_indices      Array(UInt8),            -- [0, 1, 2, 3] (GPU indices per node)
    cpu_count        UInt16,
    memory_mb        UInt32,

    -- Completion info
    exit_code        Nullable(Int32),

    -- Extended data
    metadata         String                   -- JSON: command, env vars, dependencies, etc.
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(collected_at)
ORDER BY (job_id, collected_at)
TTL collected_at + INTERVAL 6 MONTH;
```

**Benefits of managing as time-series:**

```
Polling every 60 seconds builds the history of Job #84723 like this:

collected_at          | status  | gpu_count | node_list          | ...
─────────────────────┼─────────┼───────────┼────────────────────┼────
2025-07-15 09:00:12  | pending |     0     | []                 |
2025-07-15 09:01:12  | pending |     0     | []                 |  ← 2 min wait
2025-07-15 09:02:15  | running |     4     | ['gpu-node-03']    |  ← Allocated
2025-07-15 09:03:15  | running |     4     | ['gpu-node-03']    |
...
2025-07-15 21:00:45  | completed|    4     | ['gpu-node-03']    |  ← 12 hours training

With history:
  ✅ Job wait time (time difference between pending → running transition)
  ✅ Job execution time (time difference between running → completed transition)
  ✅ Which GPUs were in use at a specific time (past retrospective)
  ✅ Align with GPU Util time-series to confirm "which Job used which GPU during this time"
```

**Key Query Examples:**

```sql
-- 1) Currently running Job list (latest snapshot)
SELECT job_id, job_name, user_id, team, project, queue,
       node_list, gpu_count, gpu_indices, start_time
FROM ibs_jobs
WHERE status = 'running'
  AND collected_at = (
    SELECT max(collected_at) FROM ibs_jobs AS inner
    WHERE inner.job_id = ibs_jobs.job_id
  )
ORDER BY start_time;

-- 2) Find running Job on a specific node+GPU (core for GPU metric Enrichment)
SELECT job_id, job_name, user_id, team
FROM ibs_jobs
WHERE has(node_list, 'gpu-node-03')
  AND has(gpu_indices, 0)
  AND status = 'running'
  AND collected_at >= now() - INTERVAL 2 MINUTE
ORDER BY collected_at DESC
LIMIT 1;

-- 3) Job wait time analysis (using time-series history)
SELECT job_id, job_name, team, queue,
       min(collected_at) AS first_seen_pending,
       minIf(collected_at, status = 'running') AS first_seen_running,
       dateDiff('second', min(collected_at),
                minIf(collected_at, status = 'running')) AS wait_seconds
FROM ibs_jobs
WHERE collected_at >= now() - INTERVAL 24 HOUR
GROUP BY job_id, job_name, team, queue
HAVING minIf(collected_at, status = 'running') IS NOT NULL
ORDER BY wait_seconds DESC;

-- 4) Which Job was using which GPU at a specific time (past retrospective)
SELECT job_id, job_name, user_id, team, node_list, gpu_indices, gpu_count
FROM ibs_jobs
WHERE status = 'running'
  AND collected_at >= '2025-07-15 14:00:00'
  AND collected_at <  '2025-07-15 14:02:00'
ORDER BY job_id;

-- 5) GPU usage time per team (GPU-Hours) calculation
SELECT team,
       countDistinct(job_id) AS job_count,
       sum(gpu_count) * 60 / 3600 AS gpu_hours  -- 60s interval × GPU count
FROM ibs_jobs
WHERE status = 'running'
  AND collected_at >= now() - INTERVAL 24 HOUR
GROUP BY team
ORDER BY gpu_hours DESC;
```

### 8.8 IBS Node Status Table — Current Value (v4.1)

**Storage Strategy: Current Value (ReplacingMergeTree)** — Only the latest status record per node is retained. When historical data is needed, it is tracked indirectly via the ibs_jobs table.

```sql
CREATE TABLE ibs_nodes (
    -- Collection timestamp (version column for ReplacingMergeTree)
    collected_at     DateTime64(3),

    -- Node identification (ORDER BY key = unique key)
    node_id          String,                  -- Node hostname

    -- Cluster/Pool membership
    cluster_id       LowCardinality(String),  -- IBS cluster identifier
    pool             LowCardinality(String),  -- Logical Node Pool name

    -- Status
    state            LowCardinality(String),  -- idle, alloc, mixed, drain, down
    partition        LowCardinality(String),  -- IBS partition name

    -- Resource info (current value)
    gpu_total        UInt8,
    gpu_alloc        UInt8,                   -- Currently allocated GPU count
    gpu_type         LowCardinality(String),  -- GPU model name
    cpu_total        UInt16,
    cpu_alloc        UInt16,
    memory_total_mb  UInt32,
    memory_alloc_mb  UInt32,

    -- Additional info
    reason           String,                  -- drain/down reason (empty string if normal)
    metadata         String                   -- JSON: additional info
) ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (node_id);
```

**Why ReplacingMergeTree:**

```
120 nodes × every 120 seconds = 86,400 rows per day → Unnecessary history accumulates quickly

ReplacingMergeTree:
  node_id is the ORDER BY key → Previous rows for the same node are automatically removed
  FINAL query always retrieves only the latest status (120 nodes → 120 rows)

  When node status history is needed:
  → Can be tracked indirectly via "Job allocation changes on this node" in the ibs_jobs table
  → If only drain/down history is needed, can record as separate events in the gpu_events table
```

**Key Query Examples:**

```sql
-- Cluster-wide GPU availability rate (current status)
SELECT cluster_id,
       sum(gpu_total) AS total_gpus,
       sum(gpu_alloc) AS allocated_gpus,
       round(sum(gpu_alloc) / sum(gpu_total) * 100, 1) AS utilization_pct,
       countIf(state = 'down') AS down_nodes,
       countIf(state = 'drain') AS drain_nodes
FROM ibs_nodes FINAL;

-- GPU availability by Pool
SELECT pool, gpu_type,
       count() AS node_count,
       sum(gpu_total) AS total_gpus,
       sum(gpu_total - gpu_alloc) AS available_gpus
FROM ibs_nodes FINAL
WHERE state NOT IN ('down', 'drain')
GROUP BY pool, gpu_type;

-- Failed/maintenance node list
SELECT node_id, state, reason, pool, collected_at
FROM ibs_nodes FINAL
WHERE state IN ('down', 'drain')
ORDER BY collected_at;
```

### 8.9 IBS Project Info Table — Snapshot (v4.1 New)

**Storage Strategy: Snapshot (ReplacingMergeTree)** — Since Project settings change infrequently, only snapshots at the time of change are retained. It has a flexible structure centered on JSON.

```sql
CREATE TABLE ibs_projects (
    -- Collection timestamp (version column)
    collected_at     DateTime64(3),

    -- Project identification (ORDER BY key)
    project_id       String,                  -- IBS Project unique ID/name
    cluster_id       LowCardinality(String),  -- IBS cluster identifier

    -- Basic info
    project_name     String,                  -- Project display name
    description      String,                  -- Description
    owner            LowCardinality(String),  -- Project owner or team
    status           LowCardinality(String),  -- active, suspended, archived

    -- FairShare / Limit / License (flexibly managed as JSON)
    fairshare_config String,                  -- JSON: Full FairShare configuration
    -- e.g.: {
    --   "weight": 100,
    --   "max_share": 0.3,
    --   "priority": "normal",
    --   "preemptable": true
    -- }

    resource_limits  String,                  -- JSON: Resource limit settings
    -- e.g.: {
    --   "max_gpus": 64,
    --   "max_jobs": 20,
    --   "max_running_jobs": 10,
    --   "max_gpus_per_job": 16,
    --   "max_walltime_hours": 168,
    --   "allowed_queues": ["default", "high-priority"],
    --   "allowed_pools": ["pool-a100", "pool-h100"]
    -- }

    license_config   String,                  -- JSON: License information
    -- e.g.: {
    --   "sw_licenses": {
    --     "cuda_toolkit": {"type": "site", "count": -1},
    --     "nccl": {"type": "site", "count": -1}
    --   },
    --   "feature_flags": ["multi_node", "preemption"]
    -- }

    -- Full original (raw IBS API response)
    raw_config       String                   -- JSON: Full raw IBS API response
) ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (project_id, cluster_id);
```

**Key Query Examples:**

```sql
-- Overall Project FairShare status
SELECT project_id, project_name, owner,
       JSONExtractInt(fairshare_config, 'weight') AS weight,
       JSONExtractFloat(fairshare_config, 'max_share') AS max_share,
       JSONExtractInt(resource_limits, 'max_gpus') AS max_gpus,
       JSONExtractInt(resource_limits, 'max_running_jobs') AS max_running_jobs
FROM ibs_projects FINAL
WHERE status = 'active'
ORDER BY weight DESC;

-- Actual GPU utilization compared to FairShare (joined with ibs_jobs)
SELECT p.project_id, p.project_name,
       JSONExtractInt(p.resource_limits, 'max_gpus') AS limit_gpus,
       count(DISTINCT j.job_id) AS running_jobs,
       sum(j.gpu_count) AS current_gpus
FROM ibs_projects FINAL AS p
LEFT JOIN (
    SELECT project, job_id, gpu_count
    FROM ibs_jobs
    WHERE status = 'running'
      AND collected_at >= now() - INTERVAL 2 MINUTE
) AS j ON p.project_id = j.project
GROUP BY p.project_id, p.project_name, p.resource_limits;
```

### 8.10 IBS Pool Information Table — Snapshot (v4.1 New)

**Storage Strategy: Snapshot (ReplacingMergeTree)** — Pool configuration (which nodes belong to which Logical Pool) changes infrequently, so it is managed as snapshots.

```sql
CREATE TABLE ibs_pools (
    -- Collection timestamp (version column)
    collected_at     DateTime64(3),

    -- Pool identification (ORDER BY key)
    pool_id          String,                  -- Logical Node Pool name/ID
    cluster_id       LowCardinality(String),  -- IBS cluster identifier

    -- Basic information
    pool_name        String,                  -- Pool display name
    description      String,
    status           LowCardinality(String),  -- active, maintenance, disabled

    -- Pool configuration (flexibly managed as JSON)
    node_list        String,                  -- JSON: List of nodes in the Pool
    -- e.g.: {
    --   "nodes": ["gpu-node-01", "gpu-node-02", ..., "gpu-node-16"],
    --   "count": 16
    -- }

    gpu_config       String,                  -- JSON: GPU configuration of the Pool
    -- e.g.: {
    --   "gpu_type": "H100",
    --   "gpus_per_node": 8,
    --   "total_gpus": 128,
    --   "interconnect": "NVLink",
    --   "network": "InfiniBand NDR"
    -- }

    scheduling_policy String,                 -- JSON: Scheduling policy
    -- e.g.: {
    --   "default_queue": "default",
    --   "allowed_projects": ["proj-a", "proj-b"],
    --   "exclusive_mode": true,
    --   "preemption_enabled": true,
    --   "max_job_walltime_hours": 168
    -- }

    -- Full original
    raw_config       String                   -- JSON: Full raw IBS API response
) ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (pool_id, cluster_id);
```

**Key Query Examples:**

```sql
-- Overall Pool status (node count, GPU count)
SELECT pool_id, pool_name,
       JSONExtractString(gpu_config, 'gpu_type') AS gpu_type,
       JSONExtractInt(gpu_config, 'total_gpus') AS total_gpus,
       JSONLength(JSONExtractRaw(node_list, 'nodes')) AS node_count,
       status
FROM ibs_pools FINAL
WHERE status = 'active';

-- Actual utilization per Pool (joined with ibs_nodes)
SELECT p.pool_id, p.pool_name,
       JSONExtractInt(p.gpu_config, 'total_gpus') AS total_gpus,
       sum(n.gpu_alloc) AS allocated_gpus,
       round(sum(n.gpu_alloc) / JSONExtractInt(p.gpu_config, 'total_gpus') * 100, 1) AS util_pct
FROM ibs_pools FINAL AS p
JOIN ibs_nodes FINAL AS n ON n.pool = p.pool_id
GROUP BY p.pool_id, p.pool_name, p.gpu_config;

-- Detailed nodes belonging to a specific Pool
SELECT n.node_id, n.state, n.gpu_total, n.gpu_alloc, n.gpu_type
FROM ibs_nodes FINAL AS n
WHERE n.pool = 'pool-h100-cluster-a'
ORDER BY n.node_id;
```

### 8.11 VMware VM Inventory Table (v4)

```sql
CREATE TABLE vmware_vm_inventory (
    collected_at     DateTime64(3),
    vm_name          String,
    vm_uuid          String,
    vm_status        LowCardinality(String),
    esxi_host        String,
    cluster          LowCardinality(String),
    resource_pool    LowCardinality(String),
    datacenter       LowCardinality(String),
    vcpu_count       UInt16,
    memory_mb        UInt32,
    guest_os         LowCardinality(String),
    gpu_count        UInt8,
    gpu_type         LowCardinality(String),
    gpu_profile      String,
    gpu_pci_ids      String,
    annotation       String,
    folder           String,
    metadata         String
) ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (vm_uuid)
TTL collected_at + INTERVAL 12 MONTH;
```

### 8.12 Table Summary

| # | Table | Engine | Storage Strategy | Data Source | TTL | Notes |
|---|---|---|---|---|---|---|
| 1 | `gpu_unified_logs` | MergeTree | Time-series | Vector | 6 months | Unified logs across all environments |
| 2 | `gpu_demand` | MergeTree | Time-series | Ingestion API | - | Demand analysis |
| 3 | `gpu_inventory` | ReplacingMT | Current value | Ingestion API | - | GPU assets |
| 4 | `gpu_events` | MergeTree | Time-series | Zabbix Webhook | 1 year | HW events |
| 5 | `gpu_profiling_traces` | MergeTree | Time-series | Profiling Controller | 3 months | L3 kernel data |
| 6 | `gpu_profiling_sessions` | ReplacingMT | Current value | Profiling Controller | - | L3 session summary |
| 7 | **`ibs_jobs`** | **MergeTree** | **Time-series** | Metadata Collector | 6 months | **Job lifecycle history** |
| 8 | **`ibs_nodes`** | **ReplacingMT** | **Current value** | Metadata Collector | - | **Latest node state** |
| 9 | **`ibs_projects`** | **ReplacingMT** | **Snapshot** | Metadata Collector | - | **FairShare/Limit/License (JSON)** |
| 10 | **`ibs_pools`** | **ReplacingMT** | **Snapshot** | Metadata Collector | - | **Logical Node Pool configuration (JSON)** |
| 11 | **`vmware_vm_inventory`** | ReplacingMT | Current value | Metadata Collector | 12 months | VMware GPU VM |

---

## 9. K8s Deployment Strategy

### 9.1 Namespace Structure

```
monitoring/                        # Metric collection/storage/alerting
  ├── victoriametrics-*            # VM cluster
  ├── vmagent-central-*            # ★ v5: Central Pull scraper (HA)
  ├── vmalert-*                    # Alert rule engine
  └── alertmanager-*               # Alert routing

clickhouse/                        # ClickHouse dedicated
  ├── clickhouse-operator
  └── clickhouse-cluster-*

logging/                           # Log collection
  ├── vector-aggregator-*          # ★ v5: Central log receiver/parser/loader
  └── vector-agent-*               # ★ v5: Lightweight Agent on K8s nodes (DaemonSet)

visualization/                     # Visualization
  └── grafana-*

profiling/                         # L3 Profiling
  ├── profiling-controller-*
  └── profiling-cronjob-*

metadata/                          # Metadata collection
  └── metadata-collector-*         # Metadata Collector Deployment

ingestion/                         # Custom data ingestion (optional)
  └── gpu-ingestion-api-*
```

### 9.2 Deployment Tools and Methods

| Component | K8s Internal | Baremetal/VM | Notes |
|---|---|---|---|
| VictoriaMetrics | VictoriaMetrics Operator (Helm) | - | Deployed on K8s only |
| ClickHouse | Altinity ClickHouse Operator (Helm) | - | Deployed on K8s only |
| Grafana | Helm Chart | - | Deployed on K8s only |
| vmalert | VictoriaMetrics Operator | - | Deployed on K8s only |
| Alertmanager | Helm Chart | - | Deployed on K8s only |
| **vmagent (Central)** *(v5)* | **Deployment (HA, 2+ replicas)** | - | **K8s only, File SD ConfigMap** |
| **Vector Aggregator** *(v5)* | **Deployment** | - | **K8s only, log receiving/parsing** |
| Profiling Controller | Deployment + Service | - | Deployed on K8s only |
| Metadata Collector | Deployment (1 replica) | - | K8s only, ConfigMap + Secret |
| DCGM Exporter | DaemonSet (GPU nodes) | systemd (Ansible) | **Pull standby (:9400)** |
| node_exporter | DaemonSet (all nodes) | systemd (Ansible) | **Pull standby (:9100)** |
| **Vector Agent** *(v5)* | DaemonSet | systemd (Ansible) | **Lightweight, forward only** |
| ~~vmagent~~ | ~~DaemonSet~~ | ~~systemd~~ | **Removed in v5 (from nodes)** |
| kube-state-metrics | Deployment (1 instance) | - | K8s only |

### 9.3 Resource Guidelines (Initial Scale)

> Baseline: 50-100 GPU nodes (combined Baremetal + K8s + VM), metric collection interval 15 seconds

**K8s Internal (Central collection/storage/serving components):**

| Component | CPU Request | Memory Request | Storage | Notes |
|---|---|---|---|---|
| VictoriaMetrics vmstorage (x2) | 2 core | 4Gi | 500Gi SSD (PVC) | |
| VictoriaMetrics vminsert (x2) | 1 core | 2Gi | - | |
| VictoriaMetrics vmselect (x2) | 1 core | 2Gi | - | |
| ClickHouse (x2 shard, x2 replica) | 4 core | 16Gi | 1Ti SSD (PVC) | |
| Grafana | 0.5 core | 512Mi | 10Gi | |
| vmalert | 0.25 core | 256Mi | - | |
| Alertmanager | 0.25 core | 256Mi | - | |
| **vmagent Central (x2 HA)** *(v5)* | **1 core** | **1Gi** | **-** | **Pull scrape for 100 nodes** |
| **Vector Aggregator** *(v5)* | **1 core** | **1Gi** | **-** | **Log receiving/parsing/CH sink** |
| Profiling Controller | 0.5 core | 512Mi | 50Gi | |
| Metadata Collector | 0.25 core | 256Mi | - | |

**Per GPU Node Agents (v5, common across Baremetal/K8s/VM):**

| Agent | CPU | Memory | vs v4 | Notes |
|---|---|---|---|---|
| DCGM Exporter (L1+L2) | 0.10 core | 128Mi | Same | Pull standby only |
| node_exporter | 0.05 core | 64Mi | Same | Pull standby only |
| Vector Agent (lightweight) | 0.10 core | 128Mi | **-50%** | Forward only, no parsing |
| ~~vmagent~~ | ~~0.25 core~~ | ~~256Mi~~ | **Removed** | Removed in v5 |
| **Per-node total** | **~0.25 core** | **~320Mi** | **-57%** | v4: ~0.75 core, ~768Mi |

> **Resource Trade-off**: Per-node resources are reduced by more than half, while resources are concentrated on the central vmagent/Vector Aggregator. The total amount is similar, but workload interference on GPU nodes is significantly reduced.

---

## 10. Phased Build Roadmap

### Phase 1: Foundation - L1+L2 Metric Pipeline (2-3 weeks)

**Goal**: GPU metrics (L1+L2) from all environments are stored in VictoriaMetrics via central Pull and queryable in Grafana

- [ ] Prepare K8s cluster (namespaces, storage classes, RBAC)
- [ ] Install VictoriaMetrics Operator and deploy VMCluster
- [ ] Write DCGM Exporter custom counter CSV (L1 + L2 Profiling enabled)
- [ ] K8s environment: Deploy DCGM Exporter + node_exporter DaemonSet (**no vmagent**)
- [ ] Baremetal environment: Install DCGM Exporter + node_exporter via systemd (Ansible) (**no vmagent**)
- [ ] **Deploy central vmagent (K8s Deployment, HA) (v5)**
  - [ ] Write File SD target file (Baremetal/VM node list)
  - [ ] Configure K8s SD (K8s Pod auto-discovery)
  - [ ] Apply standard labels (env, cluster, node, gpu, gpu_model, workload_type)
  - [ ] remote_write -> VictoriaMetrics connection
- [ ] **Firewall verification**: Confirm central vmagent -> each node :9400/:9100 access
- [ ] Deploy Grafana + connect VictoriaMetrics datasource
- [ ] Build GPU Health (L1) + GPU Efficiency (L2) dashboards
- [ ] **Verify all nodes are up via vmagent /targets UI**

**Completion Criteria**: GPU Util + Tensor Core Active + DRAM Active viewable in Grafana while switching via `env` dropdown, all vmagent targets up

### Phase 2: Log Pipeline + Inference Metrics (2-3 weeks)

**Goal**: Build lightweight log Push pipeline + Pull collection of inference server metrics

- [ ] Install ClickHouse Operator and deploy cluster
- [ ] Create gpu_unified_logs table
- [ ] **Deploy Vector Aggregator (K8s Deployment)** — parsing/transformation/ClickHouse sink
- [ ] **K8s: Deploy Vector Agent DaemonSet** — lightweight forward only
- [ ] **Baremetal: Install Vector Agent via systemd** (Ansible) — lightweight forward only
- [ ] **Add inference server /metrics to central vmagent's File SD** (Pull)
- [ ] Configure NCCL log collection pipeline (Vector Agent -> Aggregator)
- [ ] Add ClickHouse datasource to Grafana + log dashboard
- [ ] Inference SLA dashboard + Training Communication dashboard

**Completion Criteria**: Inference server TTFT/KV Cache viewable in real-time in Grafana, logs confirmed loading to ClickHouse via Vector Aggregator

### Phase 3: Analytics & Legacy Metadata Integration (3-4 weeks) <- v4 Extension

**Goal**: Load demand data/inventory + **Collect legacy metadata (IBS, VMware) and combine with GPU metrics** + **DCGM Job Stats integration**

- [ ] Create gpu_demand, gpu_inventory, gpu_events tables
- [ ] **Create ibs_jobs, ibs_nodes, ibs_projects, ibs_pools, vmware_vm_inventory tables (v4.1)**
- [ ] **Develop Metadata Collector (v4)**
  - [ ] IBS Jobs Adapter (time-series), IBS Nodes Adapter (current value)
  - [ ] IBS Projects/Pools Adapter (snapshot, JSON)
  - [ ] VMware Adapter implementation (pyVmomi)
  - [ ] Schema Normalizer + ClickHouse Writer
  - [ ] K8s Deployment + ConfigMap + Secret manifests
- [ ] **Deploy Metadata Collector and verify data collection**
- [ ] **DCGM Job Stats integration (L2.5, no CUPTI conflict)**
  - [ ] Design IBS Job lifecycle hook (Job start -> dcgmi stats -s / Job end -> dcgmi stats -x)
  - [ ] Load Job Stats results -> ClickHouse (using gpu_profiling_sessions table)
  - [ ] Per-job GPU efficiency summary dashboard panel
- [ ] Develop and deploy GPU demand data Ingestion API
- [ ] (Optional) Zabbix Webhook -> gpu_events integration
- [ ] Deploy agents for VM environment
- [ ] **Build Job Explorer dashboard**: IBS Job<->GPU metric combination + DCGM Job Stats
- [ ] **Build VM GPU Inventory dashboard**: VMware VM status

**Completion Criteria**: In Grafana, able to view IBS Job info running on "gpu-node-03's GPU 0", view per-job GPU Util summary, and query VMware GPU VM inventory

### Phase 4: Alerting & Hardening (1-2 weeks)

**Goal**: Activate alerting system, data retention/backup policies

- [ ] Write vmalert L1/L2 rules
- [ ] Write vmalert inference SLA rules
- [ ] Alertmanager integration (Slack/Email)
- [ ] ClickHouse TTL and partition management
- [ ] VictoriaMetrics retention/downsampling
- [ ] Backup strategy

**Completion Criteria**: L1/L2 alerts verified working, data retention policies applied

### Phase 5: L3 Modular Profiling System (3-4 weeks)

**Goal**: On-demand L3 profiling + automatic triggers + **CUPTI conflict management**

- [ ] Create gpu_profiling_traces, gpu_profiling_sessions tables
- [ ] Develop Profiling Controller
  - [ ] REST API + module management
  - [ ] **L2 DCGM Profiling pause/resume functionality** (CUPTI conflict management)
  - [ ] DCGM API integration: `dcgmi profile --pause/--resume` or dynamic counter CSV replacement
- [ ] Implement Module A (PyTorch Profiler) / Module B (Nsight Systems)
  - [ ] Note: Module C (CUPTI Wrapper) priority lowered — replaced by Module B
- [ ] Result Processor -> ClickHouse loading
- [ ] vmalert -> Profiling Controller automatic trigger
- [ ] Grafana Profiling Analysis dashboard (including L2 gap display)

**Completion Criteria**: L2 automatic pause/resume works during manual profiling via REST API, vmalert automatic trigger works

### Phase 6: Advanced (Ongoing)

- [ ] **Metric Enrichment via vmagent (v4)**: Metadata Collector exposes GPU<->Job mapping via /metrics, vmagent dynamically adds job_id label to DCGM metrics
- [ ] **Additional Metadata Collector Adapters (v4)**: LDAP/AD (user->organization mapping), CMDB, etc.
- [ ] Kafka adoption review
- [ ] GPU Goodput metric custom collector
- [ ] Automated GPU Health & Efficiency Report
- [ ] RL-based scheduler data integration
- [ ] Additional environment onboarding automation
- [ ] Automatic optimization recommendations based on profiling results

---

## 11. Grafana Dashboard Design

| Dashboard | Data Source | Metric Level | Key Panels |
|---|---|---|---|
| **GPU Health** | VictoriaMetrics | L1 | GPU Util, Memory, Temp, Power, Xid Errors (filtered by environment) |
| **GPU Efficiency** | VictoriaMetrics | L2 | Tensor Active, SM Occupancy, DRAM Active, NVLink BW |
| **Inference SLA** | VictoriaMetrics | L2 | TTFT (P50/P99), TPOT, KV Cache Util, Queue Length, Batch Size |
| **Training Communication** | ClickHouse | L2 | NCCL AllReduce time, communication/computation ratio, NVLink utilization |
| **Profiling Analysis** | ClickHouse | L3 | Per-session kernel analysis, Top-K kernels, Compute Ratio, session comparison |
| **Demand & Capacity** | ClickHouse | - | GPU demand trends, per-team usage, inventory status |
| **System Overview** | VictoriaMetrics | - | Per-environment node status, CPU/Mem/Disk/Network |
| ★ **Job Explorer** *(v4)* | **ClickHouse + VM** | - | **IBS Job<->GPU mapping, per-team/per-user GPU utilization, job wait time, queue status** |
| ★ **VM GPU Inventory** *(v4)* | **ClickHouse** | - | **VMware GPU VM list, distribution per ESXi Host, allocation per Resource Pool, VM status history** |

### Job Explorer Dashboard Details (v4)

| Panel | Data Source | Description |
|---|---|---|
| Running Jobs Table | ClickHouse (ibs_jobs) | Job ID, name, user, team, node, GPU count, elapsed time |
| Per-Job GPU Util Heatmap | VM + ClickHouse Join | Average Util of GPUs used by each job |
| Per-Team GPU Allocation Bar Chart | ClickHouse (ibs_jobs) | Current GPU allocation count per team |
| Queue Pending Job Count | ClickHouse (ibs_jobs) | Number of pending status jobs by queue |
| Node Status Map | ClickHouse (ibs_nodes) | idle/alloc/drain/down visualization |
| Job History Timeline | ClickHouse (ibs_jobs) | Job execution history for a specific node/GPU |

### VM GPU Inventory Dashboard Details (v4)

| Panel | Data Source | Description |
|---|---|---|
| GPU VM List Table | ClickHouse (vmware_vm_inventory) | VM name, Host, GPU type, count, status, Resource Pool |
| GPU Distribution per ESXi Host | ClickHouse (vmware_vm_inventory) | GPU VM count per host, GPU types |
| Allocation per Resource Pool Pie | ClickHouse (vmware_vm_inventory) | GPU allocation ratio per team/project |
| VM GPU Util (combined) | VM + ClickHouse Join | VM GPU Util mapping (when DCGM collection is running inside VM) |

---

## 12. Key Design Decisions

| # | Decision | Options | Recommendation | Rationale |
|---|---|---|---|---|
| D1 | Metric collector | Prometheus vs **vmagent** | vmagent | VM ecosystem unification, lightweight. **v5: Central deployment (Pull)** |
| D2 | Log collector | Fluent Bit vs **Vector** | Vector | Native ClickHouse sink, powerful remap. **v5: Agent/Aggregator separation** |
| D3 | Exporter standardization | Different per environment vs **Identical** | Identical | Ensures standard schema, reduces operational complexity |
| D4 | DCGM Profiling activation | L1 only vs **L1+L2** | L1+L2 | Continuous efficiency analysis at 1-3% overhead |
| D5 | Inference metric source | DCGM only vs **DCGM+Inference Server** | DCGM+Inference Server | TTFT/KV Cache only available from inference server |
| D6 | L3 profiling approach | Always-on vs **On-demand modular** | On-demand modular | Overhead management, selective modules |
| D7 | Kafka necessity | Adopt vs Do not adopt | **Do not adopt initially** | Direct connection is sufficient at initial scale |
| D8 | ClickHouse mode | Single vs Cluster | **Situational** | Start single -> cluster as scale grows |
| D9 | VM mode | Single vs Cluster | **Cluster** | High volume of GPU + inference metrics |
| D10 | Zabbix role | Full retention vs **Reduced role** | Reduced role | Retain only IPMI/HW/SNMP |
| D11 | Baremetal deployment | Manual vs Automated | **Ansible** | Consistent config, easy updates |
| **D12** | **Metadata collection approach** *(v4)* | **Agent-based vs Central polling** | **Central polling (Metadata Collector)** | **Collects all metadata with 1 Deployment without adding agents to nodes. Polling is suitable since IBS/vCenter have API servers** |
| **D13** | **GPU<->Job correlation approach** *(v4)* | **vmagent label injection vs Grafana query-time JOIN** | **Grafana JOIN (Phase 3), vmagent label (Phase 6)** | **Grafana JOIN is simpler to implement with sufficient real-time capability. Label injection is also easy in central vmagent Pull architecture (Phase 6)** |
| **D14** | **VMware SDK** *(v4)* | **pyVmomi vs govmomi vs REST API** | **pyVmomi (Python)** | **Official SDK, easy GPU PCI device querying, natural fit with Python implementation of Metadata Collector** |
| **D15** | **DCGM Profiling <-> CUPTI conflict** *(v4.1)* | **Keep L2 always-on vs Temporarily suspend L2 vs Dedicated L3 nodes** | **L2 temporary suspension protocol + DCGM Job Stats in parallel** | **DCGM Profiling (L2) and CUPTI-based L3 cannot run simultaneously. Phase 3 uses DCGM Job Stats (no conflict) for job-level statistics, Phase 5 temporarily suspends L2 only on target GPUs during L3 execution** |
| **D16** | **IBS metadata storage strategy** *(v4.1)* | **Single strategy vs Per-data separation** | **3-strategy separation** | **Jobs: Time-series (MergeTree) for lifecycle tracking, Nodes: Current value (ReplacingMT) for latest only, Projects/Pools: Snapshot (ReplacingMT, JSON) for configuration change history** |
| **D17** | **Module C (CUPTI Wrapper) necessity** *(v4.1)* | **Develop vs Defer** | **Priority lowered** | **Module B (Nsight Systems) is non-invasive while providing richer data. Since it uses CUPTI as well, there is little benefit in developing a separate CUPTI Wrapper** |
| **D18** | **Metric collection approach** *(v5)* | **Push (node vmagent) vs Pull (central vmagent) vs Hybrid** | **Hybrid Pull** | **Metrics: Central vmagent does Pull (node vmagent removed, 57% node resource reduction, centralized control of collection targets/intervals/labels). Logs: Node Vector Agent does lightweight Push (Pull is unsuitable for event streams like logs)** |
| **D19** | **Service Discovery** *(v5)* | **Static vs File SD vs Consul vs DNS** | **File-based SD (Ansible-managed)** | **Consul/DNS SD is difficult in airgap environments. Ansible auto-generates JSON files on node addition/removal -> vmagent detects every 60 seconds. K8s uses K8s SD for auto-discovery** |

---

## 13. Full SW Stack Summary (v5)

```
┌──────────────────────────────────────────────────────────────────────┐
│                     GPU Monitoring Platform (v5)                         │
│                                                                       │
│  Node Deployment (Exporter + Lightweight Agent, All Envs: Baremetal / K8s / VM)  │
│  ├── DCGM Exporter       - GPU metrics HTTP endpoint (:9400, L1+L2) │
│  │                          Pull standby (central vmagent scrapes)    │
│  │                          ⚠ L2 Profiling uses CUPTI                │
│  ├── node_exporter       - System metrics HTTP endpoint (:9100)      │
│  │                          Pull standby                              │
│  ├── Vector Agent        - Lightweight log forward (no parsing)      │
│  │                          → Push to central Vector Aggregator      │
│  ├── Inference Server /metrics - Inference metrics HTTP endpoint (:8080) │
│  │                          Pull standby                              │
│  └── ※ No vmagent!       - Removed from nodes in v5 (moved to central) │
│                                                                       │
│  ★ Central Collection Layer (K8s Deployed, v5 Core)                  │
│  ├── vmagent (Central)   - Pull scrapes all node Exporters           │
│  │    HA 2+ replicas       File SD (Ansible) + K8s SD                │
│  │                          → remote_write → VictoriaMetrics         │
│  └── Vector Aggregator   - Receives logs from node Vector Agents     │
│                              Parsing / remap / standardization        │
│                              → ClickHouse sink                       │
│                                                                       │
│  K8s-only Collection                                                  │
│  └── kube-state-metrics  - K8s object state (central vmagent Pulls)  │
│                                                                       │
│  Legacy Metadata Collection (v4~)                                     │
│  └── Metadata Collector  - IBS + VMware API polling → ClickHouse      │
│      ├── IBS Adapters     - Jobs (time-series), Nodes (current value),│
│      │                     Projects/Pools (snapshot, JSON)            │
│      └── VMware Adapter  - GPU VM Inventory (pyVmomi)                │
│                                                                       │
│  Storage (K8s Deployed)                                               │
│  ├── VictoriaMetrics Cluster - Time-series metrics L1+L2             │
│  └── ClickHouse Cluster      - Logs/profiling/metadata               │
│      (11 tables total)                                                │
│                                                                       │
│  Serving (K8s Deployed)                                               │
│  ├── Grafana             - Unified dashboards (9)                    │
│  ├── vmalert             - Alert rules + L3 auto-trigger             │
│  ├── Alertmanager        - Alert routing                              │
│  └── Custom Ingestion API - Demand/JSON data collection (optional)   │
│                                                                       │
│  L2.5 Job Statistics (No CUPTI Conflict)                              │
│  └── DCGM Job Stats     - IBS Job hook integration, concurrent with L2│
│                                                                       │
│  L3 On-demand Profiling (Requires L2 Temporary Suspension)           │
│  ├── Profiling Controller - L3 request management + L2 pause/resume  │
│  ├── Module A: PyTorch Profiler (training)                           │
│  ├── Module B: Nsight Systems (general-purpose, non-invasive)        │
│  └── Result Processor                                                │
│                                                                       │
│  Existing Retained (Baremetal Only)                                   │
│  └── Zabbix              - IPMI/HW/SNMP only (reduced role)          │
│                                                                       │
│  Buffering (Optional)                                                 │
│  └── Kafka (Strimzi)     - Message buffer at large scale             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 14. Next Steps

Once this Plan is finalized:

1. **Phase 1 deployment code** (v5 revision needed):
   - Central vmagent K8s Deployment + HA manifests
   - File SD target file (Ansible template)
   - K8s SD scrape config
   - Firewall open request (central -> each node :9400/:9100)
   - Node vmagent systemd removal Ansible playbook
2. **Phase 2 code**: Vector Aggregator K8s Deployment, Vector Agent lightweight config, ClickHouse DDL
3. **Phase 3 code** (v4~):
   - Metadata Collector project scaffolding (Python/Go)
   - IBS Adapter implementation (IBS API spec confirmation needed)
   - VMware Adapter implementation (vCenter connection info needed)
   - ClickHouse DDL (ibs_jobs, ibs_nodes, ibs_projects, ibs_pools, vmware_vm_inventory)
   - DCGM Job Stats integration (IBS Job hook)
   - K8s manifests (Deployment, ConfigMap, Secret)
   - Job Explorer / VM GPU Inventory Grafana dashboards
4. **Phase 4 code**: vmalert rules, Alertmanager config
5. **Phase 5 code**: Profiling Controller (including L2 pause/resume)

### Items Requiring Further Confirmation

| Item | Details | Owner |
|---|---|---|
| **Firewall (v5)** | Allow central vmagent K8s Pod -> each GPU node :9400/:9100 access | Platform operators |
| **IBS API Spec** | REST API endpoints, authentication method, response format | Confirm with scheduler operators |
| **IBS CLI Availability** | If API is unavailable, confirm CLI output format | Confirm with scheduler operators |
| **vCenter Connection Info** | vCenter URL, service account, GPU VM filter conditions | Confirm with VMware infra team |
| **GPU Passthrough vs vGPU** | Which approach the current environment uses (collection field differences) | Confirm with VMware infra team |
| **IBS Job<->GPU Index Mapping** | What format IBS provides GPU indices in | Confirm with scheduler operators |
| **Network Accessibility** | Firewall between K8s cluster -> IBS API, vCenter | Confirm with platform operators |

We can implement each phase together using Claude Code.
