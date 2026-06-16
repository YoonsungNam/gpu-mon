# GPU Monitoring System Planning Document (v6)

> **Goal**: Build a unified monitoring system on K8s for multi GPU Cluster and GPU VM environments
> **Confirmed DBs**: ClickHouse (logs/analytics/profiling/metadata), VictoriaMetrics (time-series metrics)
> **Core Principles**:
> - Standardize with the same Exporter + same schema across all environments (Baremetal, K8s, VM)
> - **Metric collection is Pull-based** (Central vmagent scrapes each node's Exporter)
> - **Log collection is lightweight Push** (Node Vector Agent -> Central Vector Aggregator)
> - Systematize GPU/AI metrics into 3 depth levels (L1~L3), with L3 as modular on-demand
> - Collect legacy system metadata (VMware VM, S2 Job/Node/Project/Pool) and combine (Enrich) with GPU metrics

### v6 Change History (compared to v5)

| Change | Affected Sections |
|---|---|
| **[Status] Implementation progress documented** -- Phase-by-phase gap analysis against actual codebase, with completion percentages | 1, 2 |
| **[Naming] IBS -> S2** -- Internal Batch Scheduler is implemented as "S2"; table prefix `s2_*` replaces `ibs_*` | 2, 3, 5 |
| **[Schema] Simplified S2 table designs** -- `s2_projects`/`s2_pools` use flat columns instead of JSON blobs; TTL set to 180 days instead of no-TTL | 3 |
| **[Arch] Namespace simplification** -- 7 planned namespaces consolidated to 2 (`monitoring`, `clickhouse`) for operational simplicity | 2, 4 |
| **[Arch] Dashboard JSONs decoupled from Grafana provisioning** -- Provisioning infra ready, dashboard content tracked as separate work items | 2 |
| **[Infra] Docker Compose dev stack + CI/CD fully implemented** -- macbook environment with full e2e tests, GitHub Actions with path-based change detection | 2 |
| **[Infra] Airgap deployment support added** -- Bundle scripts, image mirroring, chart caching for corp air-gapped environments | 2 |
| **[Roadmap] Revised work items** -- Phase 1-2 items reclassified into "Remaining Foundation" phase; new Phase 3-4 scoped more precisely | 5 |
| **[Decision] D20-D22 added** -- Namespace consolidation, S2 naming, simplified S2 schema rationale | 4 |

---

## 1. Implementation Status Summary

### 1.1 Overall Progress by Phase

| Phase | v5 Target | Status | Completion |
|---|---|---|---|
| **Phase 1**: Foundation (L1+L2 Metric Pipeline) | Central vmagent Pull + VictoriaMetrics + Grafana | Core pipeline working in dev/homelab | **~85%** |
| **Phase 2**: Log Pipeline + Inference Metrics | Vector push pipeline + ClickHouse + inference scraping | Log pipeline working, inference not yet connected | **~60%** |
| **Phase 3**: Analytics & Legacy Metadata | Metadata Collector + S2/VMware adapters + dashboards | App + schemas done, dashboards + ancillary tables missing | **~50%** |
| **Phase 4**: Alerting & Hardening | vmalert + Alertmanager + retention policies | Rules written, deployment not done | **~30%** |
| **Phase 5**: L3 Modular Profiling | Profiling Controller + L2 pause/resume | Not started | **0%** |
| **Phase 6**: Advanced | Kafka, LDAP, GPU Goodput, auto-reports | Not started | **0%** |

### 1.2 What Has Been Built

**Core Infrastructure (fully operational in dev/homelab):**

```
Metric Pipeline (Pull):
  mock-dcgm-exporter (:9400) -> vmagent-central -> vminsert -> vmstorage -> vmselect -> Grafana
  ✅ Working end-to-end in Docker Compose and Helmfile (homelab)

Log Pipeline (Push):
  Vector source -> Vector Aggregator (remap/standardize) -> ClickHouse (gpu_unified_logs)
  ✅ Working end-to-end in Docker Compose

Metadata Pipeline (Poll):
  S2 API -> Metadata Collector (S2 Adapter) -> ClickHouse (s2_jobs/nodes/projects/pools)
  vCenter API -> Metadata Collector (VMware Adapter) -> ClickHouse (vmware_vm_inventory)
  ✅ Application code complete, Helm chart ready, conditional deployment in Helmfile
```

**Deployment Environments:**

| Environment | Method | Status | Notes |
|---|---|---|---|
| **macbook** | Docker Compose | ✅ Full stack | `make dev-up`, mock GPU metrics, all 8 services |
| **homelab** | Helmfile + K8s | ✅ Working | Mock GPU metrics, small footprint |
| **corp** | Helmfile + K8s (airgap) | ✅ Ready | Two-repo structure, airgap bundle support |

**Custom Components Built:**

| Component | Location | Tests | Chart |
|---|---|---|---|
| mock-dcgm-exporter | `src/mock-dcgm-exporter/` | Unit + HTTP | `charts/mock-dcgm-exporter/` |
| metadata-collector | `src/metadata-collector/` | Unit (4 test files) | `charts/metadata-collector/` |
| vmagent-central | - (OSS image) | Chart tests (8 cases) | `charts/vmagent-central/` |
| clickhouse-cluster | - (OSS image) | - | `charts/clickhouse-cluster/` |

**ClickHouse Tables Created (6 of 11 planned):**

| Table | Engine | Status |
|---|---|---|
| `gpu_unified_logs` | MergeTree | ✅ Created (TTL 30d) |
| `s2_jobs` | MergeTree | ✅ Created (TTL 180d) |
| `s2_nodes` | ReplacingMergeTree | ✅ Created |
| `s2_projects` | ReplacingMergeTree | ✅ Created |
| `s2_pools` | ReplacingMergeTree | ✅ Created |
| `vmware_vm_inventory` | ReplacingMergeTree | ✅ Created (TTL 365d) |
| `gpu_demand` | MergeTree | ❌ Not created |
| `gpu_inventory` | ReplacingMergeTree | ❌ Not created |
| `gpu_events` | MergeTree | ❌ Not created |
| `gpu_profiling_traces` | MergeTree | ❌ Not created (Phase 5) |
| `gpu_profiling_sessions` | ReplacingMergeTree | ❌ Not created (Phase 5) |

**Alert Rules (written, not deployed):**

| Rule | Severity | Status |
|---|---|---|
| GPUTemperatureHigh (>85C, 5m) | warning | ✅ Rule file exists |
| GPUTemperatureCritical (>95C, 2m) | critical | ✅ Rule file exists |
| GPUXidError (any XID, 5m) | critical | ✅ Rule file exists |
| GPUPowerHigh (>400W, 10m) | warning | ✅ Rule file exists |
| GPUClockThrottle (SM<500 while util>50%, 5m) | warning | ✅ Rule file exists |
| GPULowUtilization (<10% avg, 30m) | warning | ✅ Rule file exists |
| GPUTensorCoreUnderutilized (<20% while util>80%) | info | ✅ Rule file exists |
| GPUMemoryPressure (>95%, 5m) | warning | ✅ Rule file exists |

**Grafana Dashboards (0 of 6 planned):**

Dashboard provisioning infrastructure is configured, but no actual JSON dashboard files have been created yet. The `dashboards/` directory contains only a README listing the planned dashboards.

**CI/CD (fully operational):**

| Workflow | Purpose |
|---|---|
| `unit-tests.yml` | pytest (src/ & tests/) |
| `lint.yml` | Helm chart + Helmfile + Python (ruff) linting |
| `helm-tests.yml` | Helm chart unit tests |
| `component-e2e-tests.yml` | Docker Compose integration + e2e |
| `docker-build-push.yml` | Build + push images to GHCR |
| `check-no-corp.yml` | Prevent corp-specific paths in PRs |
| `cleanup-ghcr.yml` | Scheduled GHCR image cleanup |

**Testing (comprehensive):**

| Layer | Files | Scope |
|---|---|---|
| Unit (src/) | `test_http.py`, `test_metrics.py`, `test_s2_adapter.py`, `test_vmware_adapter.py`, `test_scheduler.py`, `test_writer.py` | Pure Python, no external deps |
| Component | `test_vmagent.py`, `test_victoriametrics.py`, `test_clickhouse.py`, `test_vector.py`, `test_grafana.py` | Per-service smoke on Docker Compose |
| E2E | `test_e2e_metrics.py`, `test_e2e_logs.py`, `test_e2e_grafana.py` | Full pipeline validation |
| Integration | `test_ansible.py` | Ansible playbook linting |
| Chart | `deployment_test.yaml`, `configmap_test.yaml`, `service_test.yaml`, `serviceaccount_test.yaml` | Helm template rendering |

**Ansible Roles (2 of 3 planned):**

| Role | Status | Notes |
|---|---|---|
| dcgm-exporter | ✅ Implemented | systemd service deployment |
| vector-agent | ✅ Implemented | Downloads Vector, systemd service |
| node-exporter | ❌ Missing | Referenced in playbook, role dir absent |

### 1.3 Implementation Deviations from v5

| Area | v5 Plan | Actual Implementation | Rationale |
|---|---|---|---|
| **Batch Scheduler naming** | `IBS` (generic) | `S2` (actual system name) | Real system identified during implementation |
| **Table prefix** | `ibs_*` | `s2_*` | Matches actual system name |
| **Namespace count** | 7 (monitoring, clickhouse, logging, visualization, profiling, metadata, ingestion) | 2 (monitoring, clickhouse) | Operational simplicity; fewer namespaces to manage at current scale |
| **S2 project schema** | JSON blobs (`fairshare_config`, `resource_limits`, `license_config`, `raw_config`) | Flat columns (`fairshare_weight`, `gpu_limit`, `metadata`) | Simpler queries, sufficient for known S2 API fields |
| **gpu_unified_logs partitioning** | `(env, toYYYYMM(timestamp))` | `toYYYYMM(timestamp)` | Single-dimension partitioning sufficient at current log volume |
| **gpu_unified_logs TTL** | 6 months | 30 days | Conservative for initial deployment; adjustable |
| **gpu_unified_logs fields** | `env`, `cluster_id` | `deployment_env`, `platform`, `cluster_id` | More descriptive field naming; `platform` adds deployment-type distinction |

---

## 2. Remaining Work Items (Gap List)

This section lists every gap between v5 planning and current implementation, organized by priority.

### 2.1 Priority 1 -- Remaining Foundation (blocks corp deployment)

These items complete Phase 1-2 and are prerequisites for production corp deployment.

| # | Item | Phase | Category | Effort | Dependencies |
|---|---|---|---|---|---|
| W1 | Create GPU Overview (L1) dashboard JSON | P1 | Dashboard | S | Grafana VM datasource |
| W2 | Create GPU Efficiency (L2) dashboard JSON | P1 | Dashboard | S | Grafana VM datasource |
| W3 | Create Node Health dashboard JSON | P1 | Dashboard | S | Grafana VM datasource |
| W4 | Create Inference Servers dashboard JSON | P2 | Dashboard | M | Inference metrics flowing |
| W5 | Create Job Explorer dashboard JSON | P3 | Dashboard | L | S2 metadata flowing |
| W6 | Create VM Inventory dashboard JSON | P3 | Dashboard | M | VMware metadata flowing |
| W7 | node_exporter Ansible role | P1 | Ansible | S | - |
| W8 | DCGM Exporter custom counter CSV for L2 Profiling | P1 | Config | S | Real DCGM hardware |
| W9 | kube-state-metrics Helmfile release | P1 | Helm | S | - |
| W10 | node_exporter Helmfile release (or DaemonSet chart) | P1 | Helm | S | - |
| W11 | Inference server File SD targets in vmagent | P2 | Config | S | Inference servers running |
| W12 | K8s Vector Agent DaemonSet deployment | P2 | Helm | M | - |
| W13 | NCCL log parsing VRL transform | P2 | Config | M | NCCL logs available |

### 2.2 Priority 2 -- Alerting & Operations (blocks production hardening)

| # | Item | Phase | Category | Effort | Dependencies |
|---|---|---|---|---|---|
| W14 | Deploy vmalert via Helmfile | P4 | Helm | M | VM cluster running |
| W15 | Deploy Alertmanager via Helmfile | P4 | Helm | M | - |
| W16 | Write inference SLA alert rules | P4 | Config | S | Inference metrics flowing |
| W17 | ClickHouse partition management automation | P4 | Operations | M | - |
| W18 | VictoriaMetrics retention/downsampling config | P4 | Config | S | - |
| W19 | Backup strategy (VM snapshots + CH backups) | P4 | Operations | L | - |

### 2.3 Priority 3 -- Analytics Completion

| # | Item | Phase | Category | Effort | Dependencies |
|---|---|---|---|---|---|
| W20 | Create gpu_demand table + schema | P3 | Schema | S | - |
| W21 | Create gpu_inventory table + schema | P3 | Schema | S | - |
| W22 | Create gpu_events table + schema | P3 | Schema | S | - |
| W23 | Custom Ingestion API (gpu_demand/inventory) | P3 | Application | L | W20, W21 |
| W24 | Zabbix Webhook -> gpu_events bridge | P3 | Integration | M | W22 |
| W25 | DCGM Job Stats integration (L2.5) | P3 | Integration | L | S2 Job lifecycle hooks |

### 2.4 Priority 4 -- L3 Profiling (future phase)

| # | Item | Phase | Category | Effort | Dependencies |
|---|---|---|---|---|---|
| W26 | Create gpu_profiling_traces table | P5 | Schema | S | - |
| W27 | Create gpu_profiling_sessions table | P5 | Schema | S | - |
| W28 | Profiling Controller application | P5 | Application | XL | W26, W27 |
| W29 | L2 DCGM Profiling pause/resume protocol | P5 | Integration | L | W28, DCGM API |
| W30 | Module A: PyTorch Profiler integration | P5 | Integration | L | W28 |
| W31 | Module B: Nsight Systems integration | P5 | Integration | L | W28 |
| W32 | vmalert -> Profiling Controller auto-trigger | P5 | Integration | M | W14, W28 |
| W33 | Profiling Analysis dashboard JSON | P5 | Dashboard | M | W28 |

### 2.5 Priority 5 -- Advanced (ongoing, no timeline)

| # | Item | Phase | Category | Effort |
|---|---|---|---|---|
| W34 | Metric enrichment via vmagent label injection | P6 | Integration | L |
| W35 | LDAP/AD user->team mapping adapter | P6 | Application | M |
| W36 | Kafka message buffer (if scale requires) | P6 | Infrastructure | XL |
| W37 | GPU Goodput custom collector | P6 | Application | L |
| W38 | Automated GPU Health & Efficiency Report | P6 | Application | L |
| W39 | RL-based scheduler data integration | P6 | Integration | L |
| W40 | Training Communication dashboard JSON | P2 | Dashboard | M |

### 2.6 Effort Legend

| Size | Approximate Scope |
|---|---|
| **S** (Small) | Single file change, <50 lines, <1 hour |
| **M** (Medium) | 2-4 files, 50-200 lines, ~1 PR |
| **L** (Large) | 5+ files, 200+ lines, may split into 2-3 PRs |
| **XL** (Extra Large) | New application or major subsystem, 3+ PRs |

---

## 3. Schema Comparison (v5 Plan vs Actual)

This section documents the differences between v5's planned schemas and what was actually implemented, for reference during future schema changes.

### 3.1 S2 Jobs (`s2_jobs`, was `ibs_jobs`)

| Column | v5 Plan | Actual | Delta |
|---|---|---|---|
| `project` | ✅ LowCardinality(String) | ❌ Not present | Removed (team is sufficient for S2) |
| `gpu_indices` | ✅ Array(UInt8) | ✅ Array(UInt8) | Same |
| ORDER BY | `(job_id, collected_at)` | `(status, collected_at, job_id)` | Optimized for status-based queries |
| TTL | 6 months | 180 days | Same |

### 3.2 S2 Nodes (`s2_nodes`, was `ibs_nodes`)

| Column | v5 Plan | Actual | Delta |
|---|---|---|---|
| `cluster_id` | ✅ Present | ❌ Not present | Simplified (single-cluster assumption) |
| `pool` | ✅ LowCardinality(String) | ❌ Not present | Removed |
| `partition` | ✅ LowCardinality(String) | ✅ Present | Same |
| `state` | ✅ LowCardinality(String) | Field named `status` | Renamed |
| GPU fields | `gpu_total`, `gpu_alloc`, `gpu_type` | `gpu_total`, `gpu_allocated`, no `gpu_type` | Slight rename, no gpu_type |
| `reason` | ✅ String | ❌ Not present | Removed |

### 3.3 S2 Projects (`s2_projects`, was `ibs_projects`)

| Column | v5 Plan | Actual | Delta |
|---|---|---|---|
| `cluster_id` | ✅ ORDER BY key | ❌ Not present | Simplified |
| `description` | ✅ String | ❌ Not present | Removed |
| `owner` | ✅ LowCardinality | ❌ Not present | Removed |
| `status` | ✅ LowCardinality | ❌ Not present | Removed |
| `fairshare_config` | JSON blob | `fairshare_weight` (UInt32) | Flattened to scalar |
| `resource_limits` | JSON blob | `gpu_limit` (UInt32) | Flattened to scalar |
| `license_config` | JSON blob | ❌ Removed | Not needed |
| `raw_config` | JSON blob | `metadata` (String/JSON) | Simplified to generic metadata |

### 3.4 S2 Pools (`s2_pools`, was `ibs_pools`)

| Column | v5 Plan | Actual | Delta |
|---|---|---|---|
| `cluster_id` | ✅ ORDER BY key | ❌ Not present | Simplified |
| `description` | ✅ String | ❌ Not present | Removed |
| `status` | ✅ LowCardinality | ❌ Not present | Removed |
| `node_list` | JSON blob (`{"nodes": [...], "count": N}`) | Array(String) column `node_list` | Flattened |
| `gpu_config` | JSON blob | `gpu_total` (UInt32) | Flattened to scalar |
| `scheduling_policy` | JSON blob | ❌ Removed | Not needed initially |
| `raw_config` | JSON blob | `metadata` (String/JSON) | Simplified |

### 3.5 VMware VM Inventory (`vmware_vm_inventory`)

Implemented closely matching v5 plan. Minor differences:

| Column | v5 Plan | Actual | Delta |
|---|---|---|---|
| `datacenter` | ❌ In `metadata` JSON | ✅ Dedicated column | Promoted to top-level |
| `folder` | ❌ In `metadata` JSON | ✅ Dedicated column | Promoted to top-level |
| TTL | 12 months | 365 days | Same |

### 3.6 gpu_unified_logs

| Field | v5 Plan | Actual | Delta |
|---|---|---|---|
| `env` | LowCardinality(String) | `deployment_env` LowCardinality(String) | Renamed for clarity |
| N/A | N/A | `platform` LowCardinality(String) | Added (baremetal/k8s/vm distinction) |
| PARTITION BY | `(env, toYYYYMM(timestamp))` | `toYYYYMM(timestamp)` | Simplified |
| TTL | 6 months | 30 days | Shorter (configurable) |

---

## 4. Updated Design Decisions

All v5 decisions (D1-D19) remain in effect. The following are added or amended:

| # | Decision | Options | Choice | Rationale |
|---|---|---|---|---|
| **D20** | **Namespace consolidation** | 7 namespaces (per v5) vs 2 consolidated | **2 namespaces** (`monitoring`, `clickhouse`) | At current scale (<10 releases), separate namespaces add RBAC complexity without benefit. Can split later when team grows or isolation is needed. |
| **D21** | **Batch scheduler naming** | Generic `IBS` vs actual `S2` | **S2** | The Internal Batch Scheduler is actually called S2 in the target environment. Using the real name avoids confusion in schemas, code, and dashboards. Table prefix: `s2_*`. |
| **D22** | **S2 schema simplification** | JSON blobs (v5) vs flat columns | **Flat columns + metadata JSON** | S2 API returns well-known fields. Flat columns enable simple SQL queries without `JSONExtract*`. Unknown/extensible data goes into a generic `metadata` JSON column. Can migrate to JSON blobs if S2 API evolves with complex nested structures. |
| **D23** | **Dashboard JSON lifecycle** | Grafana UI export vs code-generated vs manual JSON | **Manual JSON committed to `dashboards/`** | Dashboards are version-controlled, reviewable in PRs, and provisioned via Grafana sidecar. Avoids UID drift from UI exports. |

---

## 5. Revised Build Roadmap

The v5 roadmap assumed a greenfield build. v6 accounts for what already exists and focuses the remaining work into concrete, PR-sized increments.

### Phase R (Remaining Foundation) -- ~2-3 weeks

**Goal**: Complete the foundation gaps from Phase 1-2 so the pipeline works end-to-end with real data in corp.

**Work items**: W1-W3, W7-W10

```
PR Sequence (suggested):

  PR-R1: node_exporter Ansible role + playbook update [W7]
         (ansible/roles/node-exporter/)

  PR-R2: kube-state-metrics Helmfile release [W9]
         (helmfile.yaml.gotmpl + environments/homelab/kube-state-metrics.yaml)

  PR-R3: node_exporter Helmfile release [W10]
         (helmfile.yaml.gotmpl or document as external dependency)

  PR-R4: DCGM Exporter L2 Profiling counter CSV [W8]
         (ansible/roles/dcgm-exporter/files/custom-counters.csv)

  PR-R5: GPU Overview (L1) dashboard JSON [W1]
         (dashboards/gpu-overview.json)

  PR-R6: GPU Efficiency (L2) dashboard JSON [W2]
         (dashboards/gpu-efficiency.json)

  PR-R7: Node Health dashboard JSON [W3]
         (dashboards/node-health.json)
```

**Completion Criteria**: All vmagent targets up in corp, L1+L2 metrics visible in Grafana dashboards with real GPU data.

### Phase I (Inference + Logs Completion) -- ~1-2 weeks

**Goal**: Connect inference server scraping, complete K8s log collection, add NCCL parsing.

**Work items**: W4, W11-W13, W40

```
PR Sequence:

  PR-I1: Inference server File SD targets [W11]
         (environments/corp/vmagent.yaml, targets/inference-servers.json)

  PR-I2: K8s Vector Agent DaemonSet [W12]
         (charts/ or helmfile.yaml.gotmpl with vector DaemonSet values)

  PR-I3: NCCL log parsing VRL transform [W13]
         (compose/vector-aggregator.toml + environments/*/vector.yaml)

  PR-I4: Inference Servers dashboard JSON [W4]
         (dashboards/inference-servers.json)

  PR-I5: Training Communication dashboard JSON [W40]
         (dashboards/training-communication.json)
```

**Completion Criteria**: Inference TTFT/TPOT/KV Cache in Grafana, NCCL logs parsed and loaded to ClickHouse, K8s pod logs flowing.

### Phase A (Alerting & Hardening) -- ~1-2 weeks

**Goal**: Deploy alerting stack, configure retention, establish backup strategy.

**Work items**: W14-W19

```
PR Sequence:

  PR-A1: vmalert Helmfile release + alert rules ConfigMap [W14]
         (helmfile.yaml.gotmpl + alerting/ integration)

  PR-A2: Alertmanager Helmfile release [W15]
         (helmfile.yaml.gotmpl + alerting/alertmanager/)

  PR-A3: Inference SLA alert rules [W16]
         (alerting/rules/inference-sla.yaml)

  PR-A4: ClickHouse partition management + VM retention [W17, W18]
         (scripts/ or environments/ config)

  PR-A5: Backup strategy documentation + scripts [W19]
         (docs/backup-strategy.md + scripts/backup-*.sh)
```

**Completion Criteria**: Alerts firing in Slack/Email, ClickHouse partitions auto-managed, backup procedure documented and tested.

### Phase D (Dashboard + Analytics Completion) -- ~2-3 weeks

**Goal**: Complete all remaining dashboards and analytics tables.

**Work items**: W5, W6, W20-W24

```
PR Sequence:

  PR-D1: gpu_demand + gpu_inventory + gpu_events schemas [W20-W22]
         (schemas/)

  PR-D2: Job Explorer dashboard JSON [W5]
         (dashboards/job-explorer.json)

  PR-D3: VM Inventory dashboard JSON [W6]
         (dashboards/vm-inventory.json)

  PR-D4: Custom Ingestion API scaffolding [W23]
         (src/ingestion-api/)

  PR-D5: Zabbix Webhook -> gpu_events bridge [W24]
         (scripts/ or src/)
```

**Completion Criteria**: All 6+ dashboards operational, analytics tables accepting data.

### Phase J (DCGM Job Stats -- L2.5) -- ~2-3 weeks

**Goal**: Per-Job GPU utilization without CUPTI conflict, integrated with S2 scheduler.

**Work items**: W25

```
PR Sequence:

  PR-J1: DCGM Job Stats integration design doc
         (docs/dcgm-job-stats.md)

  PR-J2: S2 Job lifecycle hook -> DCGM stats start/stop
         (scripts/ or src/metadata-collector/ extension)

  PR-J3: Job Stats -> ClickHouse pipeline
         (schemas/ + src/)

  PR-J4: Per-Job GPU Efficiency panel in Job Explorer dashboard
         (dashboards/job-explorer.json update)
```

**Completion Criteria**: Per-Job GPU Util summary available in Job Explorer dashboard, no CUPTI conflict with L2.

### Phase L3 (L3 Profiling System) -- ~4-6 weeks

**Goal**: On-demand kernel-level profiling with CUPTI conflict management.

**Work items**: W26-W33

```
PR Sequence:

  PR-L1: Profiling schemas [W26, W27]
         (schemas/)

  PR-L2: Profiling Controller scaffolding [W28]
         (src/profiling-controller/)

  PR-L3: L2 pause/resume protocol [W29]
         (src/profiling-controller/ DCGM API integration)

  PR-L4: Module A -- PyTorch Profiler integration [W30]
         (src/profiling-controller/modules/)

  PR-L5: Module B -- Nsight Systems integration [W31]
         (src/profiling-controller/modules/)

  PR-L6: vmalert auto-trigger [W32]
         (alerting/ + src/profiling-controller/)

  PR-L7: Profiling Analysis dashboard [W33]
         (dashboards/)
```

**Completion Criteria**: L3 profiling triggered via REST API or vmalert, L2 auto-pauses and resumes, results queryable in Grafana.

### Phase 6 (Advanced) -- Ongoing, no fixed timeline

**Work items**: W34-W39

Triggered by scale needs or stakeholder requests. See v5 Section 10 Phase 6 for details.

---

## 6. Dependency Graph

```
Phase R (Remaining Foundation)
  ├── W7  node_exporter Ansible
  ├── W8  DCGM L2 counter CSV
  ├── W9  kube-state-metrics Helmfile
  ├── W10 node_exporter Helmfile
  └── W1-W3 Dashboard JSONs (L1, L2, Node Health)
       │
       ▼
Phase I (Inference + Logs)           Phase A (Alerting)
  ├── W11 Inference File SD            ├── W14 vmalert deploy
  ├── W12 K8s Vector DaemonSet         ├── W15 Alertmanager deploy
  ├── W13 NCCL VRL transform           ├── W16 Inference SLA rules
  ├── W4  Inference dashboard           ├── W17 CH partition mgmt
  └── W40 Training Comm dashboard       ├── W18 VM retention
       │                                └── W19 Backup strategy
       ▼                                     │
Phase D (Dashboards + Analytics)              │
  ├── W20-W22 Analytics schemas               │
  ├── W5  Job Explorer dashboard              │
  ├── W6  VM Inventory dashboard              │
  ├── W23 Ingestion API                       │
  └── W24 Zabbix bridge                       │
       │                                      │
       ▼                                      │
Phase J (DCGM Job Stats L2.5) ◄──────────────┘
  └── W25 S2 Job hook + DCGM stats
       │
       ▼
Phase L3 (L3 Profiling)
  ├── W26-W27 Profiling schemas
  ├── W28 Profiling Controller
  ├── W29 L2 pause/resume
  ├── W30 Module A (PyTorch)
  ├── W31 Module B (Nsight)
  ├── W32 vmalert trigger ◄── W14
  └── W33 Profiling dashboard
       │
       ▼
Phase 6 (Advanced)
  W34-W39
```

**Notes**:
- Phase R and Phase A can run in parallel.
- Phase I depends on Phase R (vmagent targets must be configured first).
- Phase D can start after Phase R (dashboards need the base pipeline).
- Phase J requires Phase D (Job Explorer dashboard) and Phase A (vmalert for optional triggers).
- Phase L3 requires Phase J (DCGM Job Stats proves the concept) and Phase A (vmalert for auto-triggers).

---

## 7. Reference to v5

This document supplements and does not replace v5. The following v5 sections remain authoritative:

| v5 Section | Content | Status |
|---|---|---|
| 1. Data Classification | 5-type data classification + storage strategy | Unchanged |
| 2. Per-Environment Standardization | Label schema, agent matrix, Zabbix role | Unchanged (except S2 naming) |
| 3. AI Metric Hierarchy (L1/L2/L3) | Metric depth definitions, CUPTI conflict analysis | Unchanged |
| 4. Legacy System Metadata | S2/VMware adapter design, enrichment methods | Unchanged (except S2 naming) |
| 5. Architecture Overview | System architecture diagram, data flow | Unchanged |
| 6. SW Stack Configuration | Component selection rationale | Unchanged |
| 7. Data Flow Details | Pull/Push/Poll path diagrams | Unchanged |
| 8. ClickHouse Table Design | Table schemas (reference design) | Superseded by Section 3 of this doc for implemented tables |
| 9. K8s Deployment Strategy | Namespace structure, resource guidelines | Amended by D20 (namespace consolidation) |
| 10. Phased Build Roadmap | Original 6-phase plan | Superseded by Section 5 of this doc |
| 11. Grafana Dashboard Design | Dashboard panel specifications | Unchanged (used as spec for W1-W6) |
| 12. Key Design Decisions | D1-D19 | Extended by D20-D23 in this doc |
| 13. Full SW Stack Summary | Component inventory | Unchanged |
| 14. Next Steps + Confirmation Items | Pending decisions | Partially resolved (S2 API confirmed, vCenter approach confirmed) |
