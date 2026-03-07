# GPU Monitoring System Planning Document (v5)

> **목표**: 멀티 GPU Cluster 및 GPU VM 환경을 통합 모니터링하는 시스템을 K8s 위에 구축
> **확정 DB**: ClickHouse (로그/분석/프로파일링/메타데이터), VictoriaMetrics (시계열 메트릭)
> **기존 환경**: Baremetal - Zabbix + DCGM, VMware vSphere GPU VM, Samsung Batch Scheduler (S2)
> **핵심 원칙**:
> - 모든 환경(Baremetal, K8s, VM)에서 동일한 Exporter + 동일한 스키마로 표준화
> - **메트릭 수집은 Pull 기반** (중앙 vmagent가 각 노드 Exporter를 scrape)
> - **로그 수집은 경량 Push** (노드 Vector Agent → 중앙 Vector Aggregator)
> - GPU/AI 메트릭을 3단계 깊이(L1~L3)로 체계화, L3는 모듈러 온디맨드 방식
> - 레거시 시스템 메타데이터(VMware VM, S2 Job/Node/Project/Pool)를 수집하여 GPU 메트릭과 결합(Enrichment)

### v5 변경 이력 (v4 대비)

| 변경 사항 | 영향 섹션 |
|---|---|
| **[핵심] 메트릭 수집을 Push → Pull 기반으로 변경** — 노드의 vmagent 제거, 중앙 vmagent(K8s)가 모든 노드 Exporter를 직접 scrape | 2, 5, 6, 7, 9, 13 |
| **[핵심] 로그 수집을 경량 Push로 변경** — 노드 Vector Agent(경량) → 중앙 Vector Aggregator(K8s)에서 파싱/변환 | 2, 5, 6, 7, 9, 13 |
| **노드당 에이전트 4개 → 2~3개로 감소** (vmagent 제거, Vector 경량화) | 2, 9 |
| **File-based Service Discovery** — Ansible이 관리하는 타겟 파일로 scrape 대상 관리 | 2 |
| 아키텍처 다이어그램 전면 개정 — S2/VMware는 Data Sources에 통합, 경로 A(Pull)/B(Push) 표기 | 5 |
| DCGM Profiling ↔ CUPTI 충돌 관리 전략 추가 | 3 |
| S2 메타데이터 저장 전략 3분류 (시계열/현재값/스냅샷), s2_projects/s2_pools 테이블 추가 | 4, 8 |
| Per-Job GPU Utilization 측정 전략 추가, DCGM Job Stats(L2.5) 도입 | 3, 4 |
| Module C (CUPTI Wrapper) 우선순위 하향 | 3, 6 |
| 설계 결정 D15~D18 추가 | 12 |

### 이전 버전 변경 이력

<details>
<summary>v4 변경 이력 (v3 대비)</summary>

| 변경 사항 | 영향 섹션 |
|---|---|
| [신규] 섹션 4: 레거시 시스템 메타데이터 통합 — VMware vCenter GPU VM Inventory, Samsung Batch Scheduler(S2) Job/Node 메타데이터 수집 체계 | 4 (신규) |
| 데이터 분류에 Metadata 유형 추가 | 1 |
| 에이전트 매트릭스에 Metadata Collector 추가 | 2 |
| 표준 라벨에 Enrichment 라벨 추가 | 2 |
| 아키텍처에 Legacy Metadata Sources + Metadata Collector 추가 | 5 |
| ClickHouse 테이블 추가 (`s2_jobs`, `s2_nodes`, `vmware_vm_inventory`) | 8 |
| 로드맵 Phase 3에 메타데이터 통합 병합 | 10 |
| 대시보드에 Job Explorer, VM Inventory 추가 | 11 |
| 설계 결정 D12~D14 추가 | 12 |

</details>

---

## 1. 데이터 분류 및 저장 전략

모니터링 대상 데이터를 **5가지 유형**으로 분류하고, 각각 최적의 저장소에 매핑합니다.

| 데이터 유형 | 예시 | 저장소 | 이유 |
|---|---|---|---|
| **Time-Series Metrics** | GPU Util, Memory, Temp, Power, Tensor Active, 추론 Latency/Throughput | **VictoriaMetrics** | 고성능 시계열 DB, Prometheus 호환, 장기 보관에 유리 |
| **Logs (구조화/비구조화)** | Job 실행 로그, 시스템 로그, OOM/Xid 에러 로그, NCCL 통신 로그 | **ClickHouse** | 컬럼형 DB로 대량 로그의 빠른 분석 쿼리 가능 |
| **JSON/Analytical Data** | GPU 수요 데이터, 클러스터 인벤토리, SLA 리포트 | **ClickHouse** | JSON 컬럼 타입 지원, 복잡한 분석 쿼리에 최적 |
| **Profiling Traces** | 커널 실행 시간, Memory 접근 패턴, NCCL 오퍼레이션, 오퍼레이터 분석 | **ClickHouse** | 대량 이벤트 분석, 세션별 집계에 최적 |
| **Legacy Metadata** *(v4 신규)* | VMware VM Inventory, S2 Job 메타데이터, S2 Node 상태 | **ClickHouse** | Snapshot 기반 이력 관리, GPU 메트릭과 JOIN 분석에 최적 |

### 왜 이렇게 나누는가?

- **VictoriaMetrics**는 Prometheus 형태의 시계열 데이터에 특화되어 있어서, 초 단위로 수집되는 GPU 메트릭과 추론 서버 메트릭을 효율적으로 저장/쿼리합니다.
- **ClickHouse**는 대량의 로그, JSON 데이터, 프로파일링 트레이스를 INSERT하고 분석 쿼리(aggregation, filtering)를 빠르게 수행하는 OLAP DB입니다.
- **Legacy Metadata**는 시계열이 아닌 **스냅샷/이벤트 기반** 데이터입니다. "이 GPU에서 지금 어떤 Job이 돌고 있는가?", "이 VM은 어떤 ESXi 호스트에 있는가?" 같은 질문에 답하려면 ClickHouse에서 시계열 메트릭과 JOIN할 수 있어야 합니다.
- 두 DB를 분리함으로써 각각의 워크로드 특성에 맞는 최적 성능을 얻습니다.

### Legacy Metadata가 필요한 이유 (v4 핵심 동기)

현재 GPU 모니터링의 한계:

```
DCGM에서 보이는 것:            실제로 알고 싶은 것:
─────────────────              ─────────────────
GPU 0: Util 85%                 "김OO 연구원의 LLaMA 학습 Job이
GPU 0: Temp 72°C                 S2 queue 'high-priority'에서
GPU 0: Tensor Active 0.62        gpu-node-03의 GPU 0~3번을 사용 중.
                                  현재 S2 Job ID: 84723"

GPU VM에서 보이는 것:           실제로 알고 싶은 것:
─────────────────              ─────────────────
VM-01: GPU Util 45%              "vm-gpu-research-07 (GPU Passthrough A100)이
VM-01: Memory 32GB/40GB           ESXi host esxi-gpu-02.internal에서 동작 중.
                                   vCenter Resource Pool: AI-Research-Team"
```

**메타데이터를 수집하면**, GPU 메트릭에 컨텍스트가 붙어 운영과 분석의 질이 크게 올라갑니다.

---

## 2. 환경별 표준화 전략

### 2.1 핵심 원칙: Pull 기반 수집, 동일 Exporter, 동일 스키마

어떤 환경(Baremetal, K8s, VM)에서 데이터가 오든, **같은 Exporter로 노출하고 같은 스키마로 저장**합니다.

```
v5 핵심 변경: Push → Pull (Hybrid)

  Before (v4, Push 기반):
    각 GPU 노드: DCGM Exporter + node_exporter + vmagent + Vector (4개 에이전트)
    → vmagent이 로컬 scrape 후 → 중앙 VictoriaMetrics에 remote_write (Push)
    → Vector가 로그 수집 후 → 중앙 ClickHouse에 직접 sink (Push)

  After (v5, Hybrid Pull):
    각 GPU 노드: DCGM Exporter + node_exporter + Vector Agent (2~3개)
    → vmagent 제거! Exporter는 HTTP 엔드포인트만 노출 (Pull 대기)
    → 중앙 vmagent(K8s)가 모든 노드의 :9400/:9100을 직접 scrape (Pull)
    → Vector Agent는 경량으로 로그만 중앙 Aggregator에 전송 (Push 유지)

  왜 Hybrid인가?
    • 메트릭: "지금 현재값" 스냅샷 → Pull이 자연스러움
      (10초 전 값 못 읽어도 괜찮음, 다음 scrape에서 새 값)
    • 로그: "이벤트 스트림" → Push가 자연스러움
      (한 번 지나간 로그는 다시 안 옴, 유실 방지를 위해 로컬에서 밀어넣기)
```

### 2.2 환경별 배포 매트릭스 (v5)

**노드에 배포되는 에이전트 (Exporter + 경량 Agent):**

| 에이전트 | 역할 | Baremetal | K8s | VM | 비고 |
|---|---|---|---|---|---|
| **DCGM Exporter** | GPU 메트릭 HTTP 노출 (L1+L2) | ✅ systemd | ✅ DaemonSet | ✅ systemd | **Pull 대기 (:9400)** |
| **node_exporter** | 시스템 메트릭 HTTP 노출 | ✅ systemd | ✅ DaemonSet | ✅ systemd | **Pull 대기 (:9100)** |
| **Vector Agent** | 로그 수집 → 중앙 Aggregator 전송 | ✅ systemd | ✅ DaemonSet | ✅ systemd | **경량 Push (forward만)** |
| ~~**vmagent**~~ | ~~메트릭 scrape → VM 전송~~ | ❌ 제거 | ❌ 제거 | ❌ 제거 | **v5에서 제거** |

> **v4 대비 변경**: 노드에서 vmagent 제거. 메트릭 수집 제어권이 중앙으로 이동.

**중앙 (K8s)에 배포되는 컴포넌트:**

| 컴포넌트 | 역할 | 배포 방식 | 비고 |
|---|---|---|---|
| **vmagent (Central)** | 모든 노드 Exporter를 Pull scrape → VictoriaMetrics | K8s Deployment (HA) | **v5 신규** |
| **Vector Aggregator** | 노드 Vector Agent에서 로그 수신 → 파싱/변환 → ClickHouse | K8s Deployment | **v5 신규** |
| **Metadata Collector** | S2 + VMware API 폴링 → ClickHouse | K8s Deployment | v4 동일 |

추가 에이전트 (환경/역할별):

| 에이전트 | Baremetal | K8s | VM | 비고 |
|---|---|---|---|---|
| **kube-state-metrics** | - | ✅ Deployment | - | K8s 전용 (Pod/Node 상태), 중앙 vmagent이 scrape |
| **추론 서버 /metrics** | ✅ | ✅ | ✅ | vLLM/TGI/Triton 자체 노출, **중앙 vmagent이 Pull** |
| **Zabbix Agent** | ✅ 유지 | - | - | IPMI/HW/SNMP 전용 (역할 축소) |

**노드 에이전트 리소스 비교:**

```
v4 (Push, 노드당 에이전트 4개):
  DCGM Exporter:  0.10 core,  128Mi
  node_exporter:  0.05 core,   64Mi
  vmagent:        0.25 core,  256Mi  ← v5에서 제거
  Vector:         0.25 core,  256Mi  ← v5에서 경량화
  합계:           0.65 core,  704Mi

v5 (Pull, 노드당 에이전트 2~3개):
  DCGM Exporter:  0.10 core,  128Mi  (동일)
  node_exporter:  0.05 core,   64Mi  (동일)
  Vector Agent:   0.10 core,  128Mi  (경량: forward만, 파싱 없음)
  합계:           0.25 core,  320Mi  (약 55% 절감)
```

### 2.3 Zabbix 역할 재정의

기존 Zabbix는 역할을 축소하고, GPU/로그 수집은 표준 에이전트로 이관합니다.

```
Before (현재):
  Zabbix = 메트릭 수집 + 알림 + 로그 감시 + 인벤토리 (만능)

After (변경 후):
  Zabbix = Baremetal 하드웨어 전용 (제한된 역할)
  └── IPMI 센서 (팬 속도, PSU 상태, 디스크 SMART)
  └── 네트워크 장비 SNMP (스위치, PDU)
  └── Baremetal 자산 인벤토리 (시리얼, 랙 위치 등 CMDB 역할)

  GPU 메트릭 수집 → DCGM Exporter (노출) + 중앙 vmagent (Pull)
  AI 워크로드 메트릭 → 추론 서버 /metrics (노출) + 중앙 vmagent (Pull)
  로그 수집       → Vector Agent (노드) → Vector Aggregator (중앙)
  알림            → vmalert + Alertmanager (표준화)
  레거시 메타데이터 → Metadata Collector (v4~)
```

### 2.4 메트릭 표준 라벨 체계 (VictoriaMetrics)

모든 환경의 메트릭에 아래 **표준 라벨**을 부여합니다. **중앙 vmagent**의 `relabel_configs`에서 일괄 관리합니다.

| 라벨 | 설명 | 예시 값 | 출처 |
|---|---|---|---|
| `env` | 인프라 환경 | `baremetal`, `k8s`, `vm` | File SD 라벨 |
| `cluster` | 클러스터 식별자 | `gpu-cluster-a`, `k8s-prod-01` | File SD 라벨 |
| `node` | 노드 호스트명 | `gpu-node-01` | SD 자동 / relabel |
| `gpu` | GPU 인덱스 | `0`, `1`, `2`, ... | DCGM 자동 |
| `gpu_model` | GPU 모델명 | `H100`, `A100`, `H200` | DCGM 자동 |
| `workload_type` | 워크로드 유형 | `training`, `inference` | File SD 라벨 |

**Enrichment 라벨 (v4~):**

GPU 메트릭 자체에는 Job 정보가 없습니다. 대신 ClickHouse의 메타데이터 테이블과 **Grafana 쿼리 시점에 JOIN**하여 컨텍스트를 부여합니다.

```
방법 1: Grafana에서 실시간 JOIN (권장)
   VictoriaMetrics: GPU Util by (node, gpu)
   + ClickHouse: s2_jobs WHERE node_id = $node AND gpu_indices HAS $gpu AND status = 'running'
   → "이 GPU에서 어떤 Job이 돌고 있는지" 대시보드에 표시

방법 2: 중앙 vmagent relabel로 라벨 주입 (선택, 복잡)
   Metadata Collector가 /metrics로 GPU↔Job 매핑을 노출
   중앙 vmagent가 이 매핑을 읽어 DCGM 메트릭에 job_id 라벨을 동적 추가
   → Pull 구조와 잘 맞음 (중앙에서 모든 것을 통제)
   → 구현 복잡도 높음 (Phase 6 고도화)
```

**중앙 vmagent Pull 설정 (v5 핵심):**

```yaml
# 중앙 vmagent config — K8s ConfigMap으로 관리
# 모든 수집 대상과 라벨을 여기서 일괄 정의

global:
  scrape_interval: 15s    # 기본 Pull 주기

# 원격 저장소
remoteWrite:
  - url: "http://vminsert.victoriametrics.svc:8480/insert/0/prometheus/"

scrape_configs:
  # ────────────────────────────────────────
  # Baremetal GPU Clusters (File SD로 관리)
  # ────────────────────────────────────────
  - job_name: "baremetal-dcgm"
    scrape_interval: 15s
    file_sd_configs:
      - files: ["/etc/vmagent/sd/baremetal-gpu-nodes.json"]
        refresh_interval: 60s    # 파일 변경 감지 주기
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
  # VM GPU Clusters (File SD로 관리)
  # ────────────────────────────────────────
  - job_name: "vm-dcgm"
    file_sd_configs:
      - files: ["/etc/vmagent/sd/vm-gpu-nodes.json"]
        refresh_interval: 60s

  # ────────────────────────────────────────
  # K8s 클러스터 (K8s SD 자동)
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
  # 추론 서버 (환경별 File SD 또는 K8s SD)
  # ────────────────────────────────────────
  - job_name: "inference-servers"
    file_sd_configs:
      - files: ["/etc/vmagent/sd/inference-servers.json"]
        refresh_interval: 60s
```

**File-based Service Discovery (Ansible 관리):**

```json
// /etc/vmagent/sd/baremetal-gpu-nodes.json
// Ansible playbook이 노드 추가/제거 시 자동 생성
[
  {
    "targets": [
      "gpu-node-01:9400", "gpu-node-02:9400", "gpu-node-03:9400",
      "gpu-node-04:9400", "gpu-node-05:9400"
      // ... 120대 노드
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
File SD 운영 흐름:

  1. 노드 추가/제거 시:
     Ansible playbook → JSON 파일 갱신 → K8s ConfigMap 업데이트
     vmagent이 refresh_interval (60초)마다 파일을 다시 읽음
     → 자동으로 새 노드 scrape 시작 / 제거된 노드 scrape 중단

  2. scrape 주기/라벨 변경 시:
     중앙 vmagent config (ConfigMap) 1곳만 수정
     → kubectl rollout restart → 전체 반영
     (120대 노드에 개별 배포할 필요 없음!)

  3. 모니터링 대상 현황 확인:
     vmagent UI (http://vmagent:8429/targets)에서
     모든 scrape 대상의 상태(up/down), 마지막 scrape 시각 확인 가능
```

이를 통해 Grafana에서 환경·워크로드 무관 통합 쿼리가 가능합니다:

```promql
# 전체 환경의 GPU 사용률 한눈에
avg(DCGM_FI_DEV_GPU_UTIL) by (env, cluster)

# 추론 워크로드만 필터
avg(DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{workload_type="inference"}) by (cluster)
```

### 2.5 로그 표준 스키마 (ClickHouse)

모든 환경의 로그를 **단일 테이블, 동일 스키마**로 저장합니다. Vector의 `remap` 변환으로 표준화합니다.

| 필드 | 타입 | 설명 | 예시 |
|---|---|---|---|
| `timestamp` | DateTime64(3) | 로그 발생 시각 | `2025-07-15 10:23:45.123` |
| `env` | LowCardinality(String) | 인프라 환경 | `baremetal`, `k8s`, `vm` |
| `cluster_id` | LowCardinality(String) | 클러스터 식별자 | `gpu-cluster-a` |
| `node_id` | String | 노드 호스트명 | `gpu-node-01` |
| `gpu_id` | Nullable(UInt8) | GPU 인덱스 (해당 시) | `0` (시스템 로그면 NULL) |
| `log_level` | LowCardinality(String) | 로그 레벨 | `INFO`, `WARN`, `ERROR`, `FATAL` |
| `source` | LowCardinality(String) | 로그 출처 | `driver`, `scheduler`, `kubelet`, `system`, `nccl` |
| `message` | String | 로그 본문 | `XID error 79 detected` |
| `metadata` | String (JSON) | 환경별 추가 데이터 | `{"pid": 1234, "container": "train-job"}` |

Vector 변환 예시 (Baremetal syslog → 표준 스키마):

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

Vector 변환 예시 (K8s Pod 로그 → 표준 스키마):

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

## 3. AI 메트릭 계층 구조 (L1 / L2 / L3)

GPU 메트릭에는 **깊이(depth)**가 있으며, AI 워크로드의 실제 효율을 파악하려면 L1만으로는 부족합니다. 학습(Training)과 추론(Inference) 모두를 고려한 3단계 체계를 적용합니다.

### 3.1 메트릭 깊이 개요

```
Level 1: 하드웨어 카운터 (상시 수집, 오버헤드 ~0%)
├── DCGM / NVML 기본 메트릭
├── GPU Utilization, Memory, Temp, Power, Xid Error
└── "GPU가 바쁜가?" → Yes/No 수준

Level 2: 효율 분석 (상시 수집, 오버헤드 1~3%)
├── DCGM Profiling Metrics (DCGM_FI_PROF_*)
├── 추론 서버 메트릭 (vLLM/TGI/Triton /metrics)
├── NCCL 통신 로그 (Vector 파싱)
├── SM Occupancy, Tensor Core Active, DRAM Activity, PCIe/NVLink BW
├── TTFT, TPOT, KV Cache, Batch Size, Queue Length
└── "GPU가 뭘 하고 있나? AI 워크로드가 효율적인가?"

Level 3: 커널/오퍼레이션 단위 (온디맨드 모듈러, 오버헤드 3~20%)
├── CUPTI Wrapper, Nsight Systems, Nsight Compute, PyTorch Profiler
├── 개별 CUDA 커널 실행 시간, 메모리 접근 패턴, 오퍼레이터별 시간
└── "어떤 커널이 왜 느린가? 병목이 정확히 어디인가?"
```

### 3.2 왜 L1만으로는 부족한가?

```
예시: H100에서 LLM 학습 중

Level 1 (DCGM 기본) 만 보면:
  GPU Utilization: 95%     ← "잘 쓰고 있네!" ...정말?

Level 2 (DCGM Profiling) 까지 보면:
  SM Occupancy: 40%        ← SM의 40%만 활성
  Tensor Core Active: 25%  ← Tensor Core는 25%만 활용!
  DRAM Read BW: 2.8 TB/s   ← 메모리 대역폭은 거의 포화
  → 결론: Memory-bound 워크로드. GPU Util 95%는 "바쁘긴 한데 비효율적"

Level 3 (CUPTI/Nsight) 까지 보면:
  Attention kernel: 45ms (Tensor 80% active)
  AllReduce kernel: 120ms  ← 이게 병목!
  Memory copy D2H: 30ms
  → 결론: 통신 오버헤드가 전체의 60%. NCCL 최적화 또는 overlap 필요
```

### 3.3 Level 1 메트릭 상세 (상시, 모든 환경)

DCGM Exporter 기본 카운터로 수집. 학습/추론 공통.

| DCGM 메트릭 | 설명 | 학습 의미 | 추론 의미 |
|---|---|---|---|
| `DCGM_FI_DEV_GPU_UTIL` | GPU 사용률 | 높을수록 좋음 | 높다고 좋은 게 아님 (idle 시 낮아야 비용 효율적) |
| `DCGM_FI_DEV_FB_USED` / `FB_FREE` | GPU 메모리 사용량 | 모델+Activation 크기 | KV Cache 크기 (동시 요청 수에 따라 변동) |
| `DCGM_FI_DEV_GPU_TEMP` | 온도 | 쓰로틀링 감지 | 동일 |
| `DCGM_FI_DEV_POWER_USAGE` | 전력 | 에너지 효율 | 동일 |
| `DCGM_FI_DEV_SM_CLOCK` | SM Clock | 쓰로틀링 감지 | 동일 |
| `DCGM_FI_DEV_PCIE_TX/RX_THROUGHPUT` | PCIe 대역폭 | 데이터 로딩 병목 | 입력 전송 병목 |
| `DCGM_FI_DEV_XID_ERRORS` | Xid 에러 | HW 결함 감지 | 동일 |

**수집 경로**: DCGM Exporter (:9400) → vmagent → VictoriaMetrics
**저장소**: VictoriaMetrics
**오버헤드**: ~0%

### 3.4 Level 2 메트릭 상세 (상시, 효율 분석)

Level 2는 3개의 소스에서 수집합니다.

#### 3.4.1 DCGM Profiling 메트릭 (학습+추론 공통)

DCGM Exporter에서 **Profiling 카운터를 활성화**하면 수집 가능합니다. 커스텀 카운터 CSV 파일로 활성화합니다.

| DCGM Profiling 메트릭 | 설명 | 학습 시 의미 | 추론 시 의미 |
|---|---|---|---|
| `DCGM_FI_PROF_SM_ACTIVE` | SM 활성 비율 | 병렬성 지표 | 배치 크기/동시 요청 반영 |
| `DCGM_FI_PROF_SM_OCCUPANCY` | SM Occupancy | 워프 스케줄링 효율 | 동일 |
| `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | Tensor Core 활용률 | 핵심 효율 지표 | Prefill에서 높고 Decode에서 **매우 낮음** (정상) |
| `DCGM_FI_PROF_PIPE_FP64_ACTIVE` | FP64 파이프 활용 | 과학 계산 워크로드 | 거의 0 (정상) |
| `DCGM_FI_PROF_PIPE_FP32_ACTIVE` | FP32 파이프 활용 | Mixed Precision 확인 | 동일 |
| `DCGM_FI_PROF_PIPE_FP16_ACTIVE` | FP16 파이프 활용 | Mixed Precision 확인 | 동일 |
| `DCGM_FI_PROF_DRAM_ACTIVE` | HBM 대역폭 활용률 | Memory-bound 판단 | Decode 시 높음 (Memory-BW bound) |
| `DCGM_FI_PROF_PCIE_TX/RX_BYTES` | PCIe 전송량 | 데이터 로딩 | 입력/출력 전송 |
| `DCGM_FI_PROF_NVLINK_TX/RX_BYTES` | NVLink 전송량 | AllReduce 통신량 | TP(Tensor Parallel) 통신량 |

**수집 경로**: DCGM Exporter (:9400, 커스텀 카운터) → vmagent → VictoriaMetrics
**저장소**: VictoriaMetrics
**오버헤드**: 1~3%

#### 3.4.2 추론 서버 메트릭 (추론 워크로드 전용)

추론 서버(vLLM, TGI, Triton 등)가 자체 `/metrics` 엔드포인트로 Prometheus 형식 메트릭을 노출합니다.

**Latency**: TTFT, TPOT, ITL, E2E Latency, Time in Queue
**Throughput**: Output Tokens/sec, Total Tokens/sec, Requests/sec, Tokens/sec/GPU
**KV Cache**: Utilization, Block Usage, Hit Rate, Eviction Count
**Batching**: Running Batch Size, Queue Length, Preemption Count
**Error/SLA**: Success/Failure Rate, SLA Violation Rate, Timeout, OOM Kill

**수집 경로**: 추론 서버 (:8000/metrics 등) → vmagent → VictoriaMetrics
**저장소**: VictoriaMetrics
**오버헤드**: ~0%

#### 3.4.3 NCCL 통신 메트릭 (분산 학습 특화)

**수집 경로**: NCCL 로그 → Vector (파싱 + 구조화) → ClickHouse
**저장소**: ClickHouse (gpu_unified_logs, source='nccl')
**오버헤드**: 낮음 (로그 출력 수준)

### 3.5 Level 3 메트릭: 모듈러 온디맨드 프로파일링

L3는 **필요할 때 활성화하고 결과를 ClickHouse에 적재**하는 모듈러 방식입니다.

#### 3.5.1 ⚠ DCGM Profiling (L2) ↔ CUPTI 기반 도구 (L3) 충돌 문제

DCGM의 L2 Profiling 메트릭(`DCGM_FI_PROF_*`)과 L3 프로파일링 도구는 모두 **NVIDIA CUPTI Activity API**를 내부적으로 사용합니다. CUPTI Activity API는 동시 소비자(subscriber) 수에 제한이 있어, L2와 L3를 **동시에 활성화하면 충돌**이 발생합니다.

```
CUPTI Activity API 충돌 구조:

  DCGM Profiling (L2 상시)          L3 프로파일링 도구
  ━━━━━━━━━━━━━━━━━━━━━━          ━━━━━━━━━━━━━━━━━━
  DCGM_FI_PROF_SM_ACTIVE    ←┐     Module A: PyTorch Profiler  ← CUPTI 사용 ✗ 충돌
  DCGM_FI_PROF_TENSOR_ACTIVE ←┤     Module B: Nsight Systems    ← CUPTI 사용 ✗ 충돌
  DCGM_FI_PROF_DRAM_ACTIVE   ←┤     Module C: CUPTI Wrapper     ← CUPTI 직접 ✗ 충돌
  (내부적으로 CUPTI 사용)    ←┘
                                     
  CUPTI Activity API: "동시 소비자 1~2개 제한"
  → L2 DCGM Profiling이 CUPTI를 점유하고 있으면
  → L3 도구가 CUPTI를 추가로 사용할 때 에러 또는 부정확한 결과

  주의: 이 충돌은 L1 기본 메트릭(GPU Util, Temp 등)에는 영향 없음
        L1은 NVML API를 사용하므로 CUPTI와 무관
```

**충돌이 발생하는 조합과 안전한 조합:**

| L2 상태 | L3 모듈 | 충돌 | 설명 |
|---|---|---|---|
| DCGM Profiling **ON** | Module A (PyTorch) | ✗ 충돌 | 둘 다 CUPTI Activity 사용 |
| DCGM Profiling **ON** | Module B (Nsight Sys) | ✗ 충돌 | Nsight도 CUPTI 기반 |
| DCGM Profiling **ON** | Module C (CUPTI) | ✗ 충돌 | 직접적 CUPTI 경쟁 |
| DCGM Profiling **OFF** (L1 only) | Module A/B/C | ✅ 안전 | CUPTI 독점 사용 가능 |
| DCGM Profiling **ON** | DCGM Job Stats | ✅ 안전 | DCGM 내부 통계 (별도 API) |

#### 3.5.2 권장 전략: L2 일시 중단 프로토콜

L3 프로파일링 시 L2 DCGM Profiling을 **대상 GPU에 한해 일시 중단**하고, 프로파일링 완료 후 재개하는 방식입니다.

```
L3 프로파일링 실행 흐름 (Profiling Controller가 관리):

  평상시:  L1 ✅  +  L2 Profiling ✅  (CUPTI: DCGM이 사용 중)
  ────────────────────────────────────────────────────────────

  ① L3 트리거 발생 (수동/vmalert/스케줄)
     Profiling Controller가 요청 접수

  ② DCGM Profiling 일시 중단 (대상 GPU만)
     Profiling Controller → DCGM API 호출:
       dcgmi profile --pause -g <gpu_group>
     또는 DCGM Exporter 카운터 CSV에서 PROF_* 제거 후 reload
     
     이 시점: L1 ✅  +  L2 Profiling ⏸  (CUPTI: 해제됨)
     → L2 Grafana 대시보드에 짧은 gap 발생 (10초~수분)
     → L1 메트릭(GPU Util, Temp 등)은 중단 없이 계속 수집

  ③ L3 프로파일링 실행
     Module A/B/C 중 선택된 모듈 실행 (CUPTI 독점 사용)
     → 10초~60초 프로파일링 세션

  ④ L3 프로파일링 완료
     결과 수집 → Result Processor → ClickHouse 적재

  ⑤ DCGM Profiling 재개
     Profiling Controller → DCGM API 호출:
       dcgmi profile --resume -g <gpu_group>
     
     이 시점: L1 ✅  +  L2 Profiling ✅  (CUPTI: DCGM이 다시 사용)

  총 L2 중단 시간: 프로파일링 세션 길이 + 전환 오버헤드 (~수초)
  → 보통 30초~2분 정도의 L2 gap (L1은 영향 없음)
```

#### 3.5.3 대안 전략: DCGM Job Stats (충돌 없음)

CUPTI 충돌을 완전히 회피하려면 DCGM 자체의 **Job Statistics 기능**을 활용할 수 있습니다. DCGM Job Stats는 CUPTI가 아닌 DCGM 내부 카운터를 사용하므로 L2 Profiling과 충돌하지 않습니다.

```
DCGM Job Stats 방식:

  DCGM에게 "이 GPU 그룹에 대해 통계를 모아라"라고 지시 → 기간 종료 후 집계 결과 수신

  Job 시작:
    dcgmi stats -s <job_id> -g <gpu_group>   # 통계 수집 시작
    
  Job 실행 중:
    DCGM이 내부적으로 GPU 메트릭 집계 (L2 Profiling과 병행 가능!)

  Job 종료:
    dcgmi stats -x <job_id> -v               # 통계 수집 종료 + 결과 출력
    → GPU Utilization (avg/max)
    → Memory Utilization (avg/max)
    → SM Clock Throttling 횟수
    → ECC 에러 수
    → Power Usage (avg/max)
    → PCIe Throughput

  장점: L2 Profiling과 동시 실행 가능, CUPTI 충돌 없음
  단점: 커널 수준 분석 불가 (커널 이름, 실행 시간 등은 볼 수 없음)
        → L3의 "깊은 분석"보다는 "Job 수준 효율 요약"에 적합
```

#### 3.5.4 전략 비교 및 권장안

| 전략 | L2 중단 | 커널 분석 | 구현 복잡도 | 권장 시점 |
|---|---|---|---|---|
| **A: L2 일시 중단 + L3** | ⏸ 짧은 gap | ✅ 가능 | 중간 | Phase 5 (L3 시스템 구축 시) |
| **B: DCGM Job Stats** | 없음 | ❌ 불가 | 낮음 | Phase 3 (S2 연동 시 함께) |
| **C: L3 전용 노드** | 없음 | ✅ 가능 | 낮음 | 여유 GPU 노드가 있는 경우 |

**권장 조합:**

```
Phase 3 (즉시):
  • DCGM Job Stats로 S2 Job별 GPU 효율 요약 수집
  • L2 Profiling과 충돌 없이 Job 수준 통계 확보
  • S2 Job lifecycle hook으로 DCGM stats start/stop 연동

Phase 5 (L3 시스템 구축):
  • L2 일시 중단 프로토콜 구현
  • Profiling Controller가 DCGM Profiling pause/resume 관리
  • Module A (PyTorch) 또는 Module B (Nsight) 위주로 운용
  • Module C (CUPTI Wrapper)는 우선순위 낮춤
    → Nsight Systems가 비침습적이면서 더 풍부한 데이터 제공

Phase 6 (고도화):
  • 전용 프로파일링 노드 지정 (L2 Profiling OFF, L3 상시 가능)
  • 또는 CUDA 12.6+ 환경에서 CUPTI 동시성 개선 확인 후 재평가
```

#### 3.5.5 모듈 비교표 (CUPTI 충돌 반영)

| | Module A: PyTorch Profiler | Module B: Nsight Systems | DCGM Job Stats |
|---|---|---|---|
| **주 대상** | 학습 | 학습 + 추론 (범용) | 학습 + 추론 (Job 요약) |
| **활성화 방식** | 환경변수 (코드 내) | CLI attach (비침습) | DCGM API (비침습) |
| **코드 수정 필요** | ✅ (환경변수 분기) | ❌ | ❌ |
| **오버헤드** | 5~10% | 5~20% | ~0% |
| **데이터 풍부도** | 오퍼레이터 단위 | 타임라인 전체 | Job 수준 집계만 |
| **CUPTI 충돌** | ✗ L2 일시 중단 필요 | ✗ L2 일시 중단 필요 | ✅ 충돌 없음 |
| **L2와 동시 사용** | ❌ | ❌ | ✅ |
| **커널 이름 확인** | ✅ | ✅ | ❌ |
| **권장 Phase** | Phase 5 | Phase 5 | **Phase 3** |

> **참고**: 기존 Module C (CUPTI Wrapper)는 우선순위를 낮춥니다. Module B (Nsight Systems)가 비침습적이면서도 CUPTI Wrapper보다 풍부한 타임라인 데이터를 제공하고, 동일하게 CUPTI를 사용하므로 별도 CUPTI Wrapper를 개발할 이유가 줄어듭니다. 추론 환경에서도 Nsight Systems `--duration=30` attach가 더 실용적입니다.

트리거: 수동 REST API, vmalert 자동 트리거, CronJob 스케줄

### 3.6 학습 + 추론 통합 메트릭 분류 총정리

| 카테고리 | 메트릭 | 학습 | 추론 | 수집원 | 저장소 | 수집 방식 |
|---|---|---|---|---|---|---|
| **L1 GPU HW** | Util, Temp, Power, Xid | ✅ | ✅ | DCGM 기본 (NVML) | VictoriaMetrics | 상시 |
| **L2 GPU 효율** | SM Active, Tensor Active, DRAM | ✅ | ✅ | DCGM Profiling (CUPTI) | VictoriaMetrics | 상시 ※ L3 시 일시 중단 |
| **L2 NVLink/PCIe** | TX/RX Bytes | ✅ | ✅ | DCGM Profiling (CUPTI) | VictoriaMetrics | 상시 ※ L3 시 일시 중단 |
| **L2 추론 Latency** | TTFT, TPOT, ITL, E2E | - | ✅ | 추론 서버 /metrics | VictoriaMetrics | 상시 |
| **L2 추론 Throughput** | Tokens/sec, Requests/sec | - | ✅ | 추론 서버 /metrics | VictoriaMetrics | 상시 |
| **L2 KV Cache** | Utilization, Hit Rate, Eviction | - | ✅ | 추론 서버 /metrics | VictoriaMetrics | 상시 |
| **L2 Batching** | Batch Size, Queue Length | - | ✅ | 추론 서버 /metrics | VictoriaMetrics | 상시 |
| **L2 NCCL 통신** | AllReduce/AllGather 시간 | ✅ | - | NCCL 로그 → Vector | ClickHouse | 상시 (로그) |
| **L2.5 Job 통계** | Job별 GPU Util/Mem 집계 | ✅ | ✅ | **DCGM Job Stats** | ClickHouse | **S2 연동 (충돌 없음)** |
| **L3 커널 분석** | 커널 시간, Memory 패턴 | ✅ | ✅ | Module A/B | ClickHouse | **온디맨드 (L2 일시 중단)** |

---

## 4. 레거시 시스템 메타데이터 통합 (v4 신규)

### 4.1 왜 레거시 메타데이터가 필요한가?

GPU 모니터링 시스템이 "GPU 0번이 바쁘다"라고만 알려주면 운영자에게 실질적인 도움이 되지 않습니다. **누가, 어떤 Job으로, 어떤 VM에서** 사용하는지까지 알아야 합니다.

```
현재: GPU 메트릭만 수집 (DCGM)
──────────────────────────
  DCGM_FI_DEV_GPU_UTIL{node="gpu-node-03", gpu="0"} = 92%

  → "gpu-node-03의 GPU 0이 92% 바쁘다"
  → 그래서 뭘 해야 하지?
  → 누가 쓰는지? 무슨 Job인지? 얼마나 더 걸리는지?

v4: GPU 메트릭 + 레거시 메타데이터 결합
───────────────────────────────────────
  GPU Util 92% + S2 Job #84723
    → 사용자: 김OO (AI연구팀)
    → Job: llama-70b-finetune
    → Queue: high-priority
    → GPU 할당: gpu-node-03 GPU 0~3
    → 제출 시각: 2025-07-15 09:00
    → 예상 완료: 2025-07-15 21:00

  GPU Util 45% + VMware VM Inventory
    → VM: vm-gpu-research-07
    → ESXi Host: esxi-gpu-02.internal
    → GPU: A100 (Passthrough)
    → Resource Pool: AI-Research-Team
    → 담당자: 이OO
```

### 4.2 대상 레거시 시스템

#### 4.2.1 Samsung Batch Scheduler (S2)

S2는 삼성 사내 GPU 클러스터용 Batch Job 스케줄러입니다. Slurm과 유사한 역할을 하며, Job 제출/스케줄링/실행/완료를 관리합니다.

**수집 대상 메타데이터 (4가지 데이터, 3가지 저장 전략):**

| 데이터 | 설명 | 저장 전략 | 활용 |
|---|---|---|---|
| **Job 정보** | Job ID, 이름, 사용자, 팀, 큐, 상태, 제출/시작/완료 시각, 할당 노드/GPU | **시계열** (MergeTree) | GPU 메트릭 결합, 대기 시간 분석, GPU-Hours 계산 |
| **Node 상태** | 노드명, 상태(idle/alloc/drain/down), 파티션, GPU 할당 현황 | **현재값** (ReplacingMT) | 클러스터 용량/가용률, 장애 노드 즉시 파악 |
| **Project 정보** | FairShare weight, 리소스 Limit, License, 허용 큐/Pool | **스냅샷** (ReplacingMT, JSON) | FairShare 대비 실제 사용률, 리소스 한도 관리 |
| **Pool 정보** | Logical Node Pool 구성, 소속 노드 목록, GPU 구성, 스케줄링 정책 | **스냅샷** (ReplacingMT, JSON) | Pool별 GPU 현황, 노드↔Pool 매핑 |

**S2 데이터 접근 방식 (우선순위):**

```
방법 1: S2 REST API 폴링 (권장)
  S2가 REST API를 제공하는 경우, Metadata Collector가 주기적으로 API를 호출.
  
  예상 엔드포인트 (S2 API 스펙에 따라 조정):
    GET /api/v1/jobs?status=running,pending,completed
    GET /api/v1/nodes
    GET /api/v1/projects
    GET /api/v1/pools

방법 2: S2 CLI 파싱
  S2 CLI (s2jobs, s2nodes 등)의 출력을 파싱하여 구조화.
  Metadata Collector가 SSH 또는 로컬에서 CLI 실행.

  예: s2jobs --format=json --state=running
      s2nodes --format=json
      s2projects --format=json
      s2pools --format=json

방법 3: S2 DB 직접 조회 (fallback)
  S2 내부 DB에 read-only 접근하여 쿼리.
  DB 스키마 의존성이 높아 유지보수 부담 있음.
```

#### 4.2.2 VMware vCenter (GPU VM Inventory)

VMware vSphere 환경에서 GPU Passthrough 또는 vGPU로 VM에 GPU를 할당합니다. vCenter API를 통해 VM↔GPU↔ESXi Host 매핑 정보를 수집합니다.

**수집 대상 메타데이터:**

| 데이터 | 설명 | 활용 |
|---|---|---|
| **VM Inventory** | VM 이름, UUID, 상태, 생성일, 담당자(annotation) | GPU VM 현황 파악, 인벤토리 관리 |
| **VM↔Host 매핑** | VM이 어떤 ESXi 호스트에서 실행 중인지 | 호스트 장애 시 영향받는 VM 식별 |
| **GPU 할당** | VM에 할당된 GPU 종류(Passthrough/vGPU), 프로파일 | VM별 GPU 리소스 추적 |
| **Resource Pool** | VM이 속한 Resource Pool / Cluster | 팀/프로젝트별 GPU 사용량 집계 |
| **성능 스냅샷** | VM CPU/Memory 사용률 (vCenter 통계) | 호스트 리소스 활용도 확인 |

**vCenter 데이터 접근 방식:**

```
방법: vCenter REST API (pyVmomi 또는 govmomi)
  vCenter 7.x+는 REST API를 제공. pyVmomi (Python SDK)로 접근.

  수집 흐름:
    1. ServiceInstance에 연결 (vCenter IP + 서비스 계정)
    2. content.viewManager로 VirtualMachine 객체 리스트 조회
    3. 각 VM의 config, runtime, guest 속성에서 메타데이터 추출
    4. config.hardware.device에서 GPU PCI 디바이스 필터링
    5. runtime.host에서 ESXi 호스트 정보 추출

  인증: 서비스 계정 (read-only 권한이면 충분)
  프로토콜: HTTPS (vCenter 443 포트)
```

#### 4.2.3 기타 확장 가능한 소스 (향후)

| 소스 | 데이터 | 비고 |
|---|---|---|
| **LDAP/AD** | 사용자→부서/팀 매핑 | S2 사용자 ID를 조직 정보로 확장 |
| **CMDB** | 자산 정보 (시리얼, 랙, IDC 위치) | Zabbix에서 관리 중인 데이터와 통합 |
| **K8s API** | Namespace, Pod, ResourceQuota | kube-state-metrics가 이미 수집 중 |

### 4.3 Metadata Collector 설계

Metadata Collector는 레거시 시스템의 API를 주기적으로 폴링하여, ClickHouse에 표준 스키마로 적재하는 **단일 컴포넌트**입니다.

#### 4.3.1 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                     Metadata Collector                           │
│                  (K8s Deployment, 1 replica)                     │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      Scheduler                             │  │
│  │  S2 Jobs:       매 60초 (running/pending) — 시계열         │  │
│  │  S2 Jobs:       매 300초 (recently_completed) — 시계열     │  │
│  │  S2 Nodes:      매 120초 — 현재값 (ReplacingMT)           │  │
│  │  S2 Projects:   매 600초 — 스냅샷 (변경 드묾)             │  │
│  │  S2 Pools:      매 600초 — 스냅샷 (변경 드묾)             │  │
│  │  VMware VMs:    매 300초 — 현재값 (ReplacingMT)           │  │
│  └────────┬──────────┬──────────┬──────────┬─────────────────┘  │
│           │          │          │          │                      │
│           ▼          ▼          ▼          ▼                      │
│  ┌─────────────┐ ┌────────┐ ┌────────────┐ ┌────────────────┐  │
│  │ S2 Adapter  │ │S2 Adap.│ │ S2 Adapter │ │ VMware Adapter │  │
│  │ (Jobs)      │ │(Nodes) │ │ (Projects  │ │ (VMs)          │  │
│  │             │ │        │ │  + Pools)   │ │                │  │
│  │ REST/CLI    │ │REST/CLI│ │ REST/CLI    │ │ pyVmomi        │  │
│  └─────┬───────┘ └───┬────┘ └─────┬──────┘ └───────┬────────┘  │
│        │             │            │                  │            │
│        └─────────────┼────────────┼──────────────────┘            │
│                           ▼                                      │
│              ┌─────────────────────┐                             │
│              │  Schema Normalizer  │                             │
│              │  표준 스키마 변환    │                             │
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
│    또는 K8s ConfigMap + Secret (vCenter 인증 등)                 │
└─────────────────────────────────────────────────────────────────┘
```

#### 4.3.2 설정 파일 구조 (config.yaml)

```yaml
# Metadata Collector Configuration
collector:
  log_level: info
  health_port: 8080

# ClickHouse 연결
clickhouse:
  endpoints:
    - clickhouse-cluster.clickhouse.svc:9000
  database: gpu_monitoring
  username: metadata_writer
  password_secret: metadata-collector-secrets  # K8s Secret 참조
  batch_size: 500
  flush_interval: 10s

# S2 스케줄러 연결
sources:
  s2:
    enabled: true
    # 방법 1: REST API
    api_url: "http://s2-master.internal:8080/api/v1"
    auth_token_secret: s2-api-token
    # 방법 2: CLI (API가 없을 때)
    # cli_mode: true
    # cli_path: /usr/local/bin/s2jobs
    # ssh_host: s2-master.internal
    # ssh_user: monitor
    # ssh_key_secret: s2-ssh-key
    
    schedules:
      jobs_running:
        interval: 60s
        storage: timeseries           # MergeTree — 시계열 이력 보관
        filters: { status: [running, pending] }
      jobs_completed:
        interval: 300s
        storage: timeseries
        filters: { status: [completed, failed, cancelled], since: "24h" }
      nodes:
        interval: 120s
        storage: current_value        # ReplacingMergeTree — 최신 상태만
      projects:
        interval: 600s
        storage: snapshot             # ReplacingMergeTree — 변경 시 갱신
      pools:
        interval: 600s
        storage: snapshot

  vmware:
    enabled: true
    vcenter_url: "https://vcenter.internal.example.com"
    username_secret: vcenter-credentials  # K8s Secret 참조
    insecure_skip_verify: false
    
    schedules:
      vm_inventory:
        interval: 300s
        # GPU가 할당된 VM만 필터
        filter: "config.hardware.device HAS VirtualPCIPassthrough OR config.hardware.device HAS SharedPassthroughVgpu"
```

#### 4.3.3 구현 언어 및 기술 스택

| 항목 | 선택 | 이유 |
|---|---|---|
| **언어** | Python (FastAPI) 또는 Go | Python: pyVmomi(VMware SDK) 활용 용이, 빠른 프로토타이핑. Go: 장기적 성능 |
| **VMware SDK** | pyVmomi | 공식 VMware Python SDK. vCenter 6.5+ 지원 |
| **S2 연동** | HTTP client / subprocess | API 방식 또는 CLI 출력 파싱 |
| **ClickHouse client** | clickhouse-driver (Python) / clickhouse-go | Batch INSERT 지원 |
| **스케줄링** | APScheduler (Python) 또는 내장 ticker (Go) | 소스별 독립 주기 관리 |
| **컨테이너 이미지** | Alpine/Distroless 기반 | 경량 이미지, 사내 레지스트리 push |

#### 4.3.4 Adapter 패턴 (확장성)

새로운 메타데이터 소스를 쉽게 추가할 수 있도록 **Adapter 인터페이스**를 정의합니다.

```python
# adapter.py (개념 코드)
from abc import ABC, abstractmethod
from typing import List, Dict

class MetadataAdapter(ABC):
    """모든 메타데이터 소스 Adapter의 베이스 클래스"""
    
    @abstractmethod
    def fetch(self) -> List[Dict]:
        """소스에서 메타데이터를 가져와 표준 dict 리스트로 반환"""
        pass
    
    @abstractmethod
    def get_table_name(self) -> str:
        """ClickHouse 대상 테이블 이름 반환"""
        pass

class S2JobsAdapter(MetadataAdapter):
    """S2 스케줄러 Job 메타데이터 수집"""
    
    def fetch(self) -> List[Dict]:
        # REST API 호출 또는 CLI 실행
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
    """VMware vCenter GPU VM Inventory 수집"""
    
    def fetch(self) -> List[Dict]:
        # pyVmomi로 VM 목록 조회
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

### 4.4 메타데이터와 GPU 메트릭의 결합 (Enrichment)

수집된 메타데이터의 진정한 가치는 **GPU 메트릭과 결합**할 때 나타납니다.

#### 4.4.1 결합 방법

```
방법 1: Grafana 쿼리 시점 JOIN (권장, Phase 3)
─────────────────────────────────────────────
  Grafana 대시보드에서 두 데이터 소스를 결합합니다.
  
  패널 A (VictoriaMetrics):
    DCGM_FI_DEV_GPU_UTIL{node="gpu-node-03", gpu="0"}
  
  패널 B (ClickHouse):
    SELECT job_id, job_name, user_id, team
    FROM s2_jobs
    WHERE has(node_list, 'gpu-node-03')
      AND has(gpu_indices, 0)
      AND status = 'running'
    ORDER BY collected_at DESC LIMIT 1
  
  결과: GPU Util 그래프 위에 현재 실행 중인 Job 정보가 표시됨

방법 2: Grafana Transformations으로 Merge
──────────────────────────────────────────
  같은 패널에서 두 쿼리를 Merge하여 테이블로 표시:
  
  Query A: VictoriaMetrics → GPU Util by (node, gpu)
  Query B: ClickHouse → s2_jobs (running) by (node_list, gpu_indices)
  Transform: Merge → node 기준으로 Join
  
  결과: | node | gpu | GPU Util | Job ID | User | Team | 테이블

방법 3: Metric Enrichment via vmagent (Phase 6 고도화)
──────────────────────────────────────────────────────
  Metadata Collector가 Prometheus 형식으로 매핑 정보를 노출:
  
    gpu_job_mapping{node="gpu-node-03", gpu="0", job_id="84723",
                    user="kim", team="ai-research"} = 1
  
  vmagent가 이 매핑을 읽어 DCGM 메트릭에 라벨 추가.
  → PromQL에서 직접 job_id로 필터 가능.
  → 구현 복잡도 높음 (metric_relabel + recording rules)
```

#### 4.4.2 활용 시나리오

**시나리오 1: "GPU Util이 낮은 Job 식별"**

```sql
-- ClickHouse: 현재 실행 중인 Job과 GPU 할당 정보
SELECT j.job_id, j.job_name, j.user_id, j.team, j.node_list, j.gpu_count
FROM s2_jobs j
WHERE j.status = 'running'
  AND j.collected_at = (SELECT max(collected_at) FROM s2_jobs WHERE job_id = j.job_id)
```

```promql
-- VictoriaMetrics: 해당 노드의 GPU Util
avg_over_time(DCGM_FI_DEV_GPU_UTIL{node=~"$node"}[5m])
```

→ 대시보드에서 **Job별 평균 GPU Util** 산출 가능

**시나리오 2: "특정 팀의 GPU 사용 현황"**

```sql
-- ClickHouse: 특정 팀의 모든 running Job
SELECT j.job_id, j.node_list, j.gpu_count,
       j.gpu_indices, j.submit_time, j.start_time
FROM s2_jobs j
WHERE j.team = 'ai-research' AND j.status = 'running'
  AND j.collected_at = (SELECT max(collected_at) FROM s2_jobs WHERE job_id = j.job_id)
```

→ 팀별 GPU 점유율, 대기 시간, 효율 리포트 생성

**시나리오 3: "VMware GPU VM 인벤토리 현황"**

```sql
-- ClickHouse: GPU가 할당된 모든 VM의 최신 스냅샷
SELECT vm_name, esxi_host, gpu_type, gpu_count,
       resource_pool, vm_status, vcpu_count, memory_mb
FROM vmware_vm_inventory
WHERE collected_at = (SELECT max(collected_at) FROM vmware_vm_inventory WHERE vm_uuid = v.vm_uuid)
ORDER BY resource_pool, vm_name
```

→ 팀별 GPU VM 할당 현황, ESXi 호스트별 GPU 분포 확인

**시나리오 4: "ESXi 호스트 장애 시 영향 범위"**

```sql
-- 특정 ESXi 호스트에서 실행 중인 GPU VM
SELECT vm_name, gpu_type, gpu_count, resource_pool, annotation
FROM vmware_vm_inventory
WHERE esxi_host = 'esxi-gpu-02.internal'
  AND vm_status = 'poweredOn'
  AND collected_at >= now() - INTERVAL 10 MINUTE
```

→ 호스트 장애 시 영향받는 VM과 담당 팀 즉시 파악

### 4.5 데이터 보관 및 이력 관리 (v4.1 개정)

```
S2 Job 데이터 [시계열, MergeTree]:
  ├── Running/Pending Job: 60초마다 → 시계열 이력 전체 보관
  ├── Completed Job: 완료 후 추가 기록
  └── 보관: 6개월 (TTL) — Job 대기 시간, GPU-Hours 분석에 활용

S2 Node 데이터 [현재값, ReplacingMergeTree]:
  ├── 120초마다 폴링 → 노드당 최신 1건만 유지
  ├── 이력 필요 시 s2_jobs에서 간접 추적 또는 gpu_events에 이벤트 기록
  └── TTL 없음 (자동 dedup으로 행 수 안정적)

S2 Project/Pool 데이터 [스냅샷, ReplacingMergeTree]:
  ├── 600초마다 폴링 → 변경 시에만 실질적 갱신
  ├── JSON 필드 중심 (FairShare, Limit, License, Node 구성 등)
  └── TTL 없음 (설정 변경 이력은 자동으로 latest만 유지)

VMware VM Inventory [현재값, ReplacingMergeTree]:
  ├── 300초마다 GPU VM 목록 → VM당 최신 1건
  └── 보관: 12개월 (TTL)

이력 분석 예시:
  "지난 3개월간 AI연구팀의 GPU 사용 패턴 변화" → s2_jobs 시계열
  "특정 Job의 대기 시간이 얼마였는지" → s2_jobs pending→running 전환
  "VM이 다른 ESXi 호스트로 마이그레이션된 이력" → vmware_vm_inventory
```

### 4.6 Per-Job GPU Utilization 측정 전략 (v4.1 신규)

GPU 환경에서 "특정 Job의 GPU Utilization"을 측정하는 것은 CPU의 `perf attach`와는 근본적으로 다릅니다. 이 차이를 이해하면 올바른 수집 전략을 세울 수 있습니다.

#### 4.6.1 CPU vs GPU: Per-Process 모니터링의 차이

```
CPU 세계:                                 GPU 세계:
─────────                                 ─────────
• CPU 코어는 OS 스케줄러가 프로세스를     • GPU는 보통 Job에게 "통째로" 독점 할당
  시분할(time-sharing)로 공유시킴            (Exclusive Mode)
• 같은 코어에서 프로세스 A와 B가          • GPU 0 = Job A 전용, GPU 1 = Job B 전용
  번갈아 실행됨                            • → Job이 GPU를 독점하면, GPU 메트릭 = Job 메트릭
• → 프로세스별 CPU 사용률을 구분하려면
  perf attach 같은 PMU 카운터 측정 필수   • GPU 공유 시 (MPS/MIG)에만 per-process 측정 필요

핵심 인사이트:
  HPC/AI 환경에서는 GPU를 Job에 Exclusive로 할당하는 것이 일반적
  → S2가 "Job #84723 → gpu-node-03의 GPU [0,1,2,3]" 을 알려주면
  → DCGM의 gpu-node-03 GPU 0~3 메트릭이 곧 Job #84723의 메트릭
  → 프로파일러 attach 불필요!
```

#### 4.6.2 시나리오별 측정 방법

**시나리오 1: GPU 독점 할당 (대부분의 HPC/AI 환경) — 간접 결합**

```
대부분의 GPU 클러스터 환경에서 채택하는 방식입니다.
S2가 각 Job에 GPU를 exclusive로 할당하므로, per-GPU 메트릭 = per-Job 메트릭입니다.

측정 흐름:
  ┌───────────────┐     ┌──────────────────────┐     ┌─────────────────┐
  │ S2 Metadata   │     │ DCGM Exporter        │     │ Grafana         │
  │               │     │                      │     │                 │
  │ Job #84723    │     │ gpu-node-03:         │     │ JOIN:           │
  │  node: gpu-03 │ ──→ │  GPU 0: Util 92%    │ ──→ │ Job #84723의   │
  │  gpus: [0,1,  │     │  GPU 1: Util 88%    │     │ 평균 GPU Util   │
  │         2,3]  │     │  GPU 2: Util 91%    │     │ = 90.5%         │
  │               │     │  GPU 3: Util 90%    │     │                 │
  └───────────────┘     └──────────────────────┘     └─────────────────┘
    (ClickHouse)           (VictoriaMetrics)           (쿼리 시점 JOIN)

구현 (Grafana 쿼리):
  1. ClickHouse에서 Job의 (node, gpu_indices) 조회
  2. VictoriaMetrics에서 해당 node+gpu의 DCGM 메트릭 쿼리
  3. Transformations로 Merge → Job별 GPU Util 표시

장점: 추가 에이전트/프로파일러 설치 불필요 (이미 수집 중인 데이터 활용)
한계: GPU 공유 환경에서는 Job별 구분 불가
```

**시나리오 2: GPU 공유 환경 (MPS / Time-sharing) — Per-Process 측정 필요**

```
같은 GPU를 여러 프로세스가 공유하는 드문 케이스입니다.
이 경우 NVIDIA가 제공하는 Per-Process 측정 기능을 활용합니다.

방법 A: NVML Accounting Mode (권장, 오버헤드 ~0%)
  ────────────────────────────────────────────────
  $ nvidia-smi -am 1  # Accounting Mode 활성화 (1회)
  
  NVML API로 PID별 GPU 사용 통계 조회:
    nvmlDeviceGetAccountingStats(device, pid)
    → gpu_utilization: PID가 GPU 커널을 실행한 시간 비율
    → memory_utilization: PID의 FB 메모리 접근 비율  
    → max_memory_usage: PID의 최대 GPU 메모리 사용량
  
  커스텀 Exporter로 Prometheus 메트릭으로 노출:
    gpu_process_util{pid="12345", gpu="0", node="gpu-03"} = 45.2
    gpu_process_memory{pid="12345", gpu="0", node="gpu-03"} = 8192
  
  S2 Job의 PID 정보와 매핑:
    s2_jobs.metadata JSON에 {"pid": 12345} 포함 시
    → gpu_process_util과 s2_jobs를 PID 기준 JOIN

방법 B: DCGM Job Stats API (스케줄러 연동)
  ────────────────────────────────────────
  DCGM에 내장된 Job 수준 통계 기능입니다.
  S2가 Job 시작/종료 시 DCGM에 알려주면, DCGM이 해당 기간의
  GPU 사용 통계를 집계합니다.
  
  연동 흐름:
    Job 시작 → S2 Hook → dcgmi stats -s <group_id>  (통계 수집 시작)
    Job 종료 → S2 Hook → dcgmi stats -x <group_id>  (통계 수집 종료)
    → 결과: 해당 Job 기간의 GPU Util, Memory, ECC 에러, SM Occupancy 등
  
  장점: GPU Util뿐 아니라 SM Clock, Memory 등 종합 통계 제공
  단점: S2의 Job lifecycle hook이 필요 (S2 운영팀 협의 필요)

방법 C: NVIDIA MIG (A100/H100 전용)
  ────────────────────────────────────
  GPU를 물리적으로 최대 7개 인스턴스로 분할.
  각 MIG 인스턴스가 독립적인 GPU처럼 동작.
  → DCGM이 MIG 인스턴스별 메트릭을 자동 노출.
  → 가장 깔끔하지만 GPU당 동시 인스턴스 수 제한.
```

#### 4.6.3 권장 구현 로드맵

```
Phase 3 (즉시 구현): 간접 결합 방식
─────────────────────────────────────
  • S2 Job 메타데이터의 (node_list, gpu_indices)와
    DCGM per-GPU 메트릭을 Grafana에서 JOIN
  • GPU 독점 할당 환경에서 이것만으로 Per-Job GPU Util 확인 가능
  • 추가 에이전트 설치 불필요
  • → 대시보드 "Job Explorer"에서 Job별 GPU Util 패널 구현

Phase 6 (고도화, 필요 시):
─────────────────────────
  선택 A: NVML Accounting Exporter (GPU 공유 환경 대응)
    • 커스텀 Prometheus Exporter 개발 (Python/Go)
    • PID별 GPU Utilization → Prometheus 메트릭 노출
    • vmagent이 scrape → VictoriaMetrics → Grafana
    • S2 Job PID와 매핑
    
  선택 B: DCGM Job Stats 연동 (S2 Hook 필요)
    • S2 Job 시작/종료 시 DCGM에 통계 수집 시작/종료 신호
    • 종료 시 집계 결과를 ClickHouse에 적재
    • 사후 분석용 (실시간 모니터링보다는 리포트 목적)
```

#### 4.6.4 Per-Job GPU Util 데이터 흐름 (Phase 3)

```
┌──────────────────┐        ┌───────────────────┐        ┌─────────────┐
│   S2 Scheduler   │        │  GPU Node          │        │   Grafana   │
│                  │        │                    │        │             │
│ Job #84723       │        │ DCGM Exporter      │        │ Query A:    │
│  node: gpu-03    │  ──→   │  GPU 0: Util 92%   │  ──→   │ ClickHouse  │
│  gpus: [0,1,2,3] │ (S2    │  GPU 1: Util 88%   │ (DCGM  │ → Job info  │
│  user: 김OO     │  meta)  │  GPU 2: Util 91%   │  →VM)  │             │
│  team: AI연구팀  │        │  GPU 3: Util 90%   │        │ Query B:    │
│                  │        │                    │        │ VictoriaM.  │
│ → ClickHouse     │        │ → VictoriaMetrics  │        │ → GPU Util  │
│   (s2_jobs)      │        │                    │        │             │
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

## 5. 전체 아키텍처 Overview (v5)

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                              DATA SOURCES                                      │
│     ※ 노드에는 Exporter + 경량 Vector Agent만 배포 (vmagent 없음)            │
│                                                                                │
│  ┌──────────────────────────┐ ┌────────────────────┐ ┌─────────────────────┐ │
│  │ Baremetal GPU Clusters    │ │ K8s GPU Clusters   │ │ GPU VMs (VMware)    │ │
│  │ (S2 Batch Scheduler)     │ │                    │ │ (vCenter 관리)      │ │
│  │                           │ │                    │ │                      │ │
│  │ [노드 - systemd 배포]    │ │ [노드 - DaemonSet] │ │ [VM - systemd 배포] │ │
│  │ • DCGM Exporter (:9400)  │ │ • DCGM Exporter    │ │ • DCGM Exporter     │ │
│  │ • node_exporter (:9100)  │ │ • node_exporter    │ │ • node_exporter     │ │
│  │ • Vector Agent (경량)    │ │ • Vector Agent     │ │ • Vector Agent      │ │
│  │   (로그 forward만)       │ │ • kube-state-m     │ │   (로그 forward만)  │ │
│  │ • 추론서버 /metrics      │ │ • 추론서버 /metrics│ │ • 추론서버 /metrics │ │
│  │                           │ │                    │ │                      │ │
│  │ [레거시 시스템]            │ │                    │ │ [레거시 시스템]      │ │
│  │ • S2 Scheduler            │ │                    │ │ • VMware vCenter     │ │
│  │   ├ Job (시계열)          │ │                    │ │   └ GPU VM Inventory │ │
│  │   ├ Node (현재값)         │ │                    │ │                      │ │
│  │   ├ Project (스냅샷)      │ │                    │ │                      │ │
│  │   └ Pool (스냅샷)         │ │                    │ │                      │ │
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
│  │  Pull: 모든 노드의 Exporter를 직접 scrape                │ │              │
│  │  ├─ File SD: Baremetal/VM 노드 (Ansible 관리 JSON)       │ │              │
│  │  ├─ K8s SD: K8s Pod/Service 자동 검색                     │ │              │
│  │  └─ Static: 추론 서버, kube-state-metrics 등              │ │              │
│  │                                                            │ │              │
│  │  → remote_write → VictoriaMetrics                         │ │              │
│  └───────────────────────────────────────────────────────────┘ │              │
│                                                                 │              │
│  ┌──────────────────────────────────────────────────────────┐  │              │
│  │  Vector Aggregator (K8s Deployment)                       ←──┘              │
│  │                                                           │                 │
│  │  ← 각 노드의 Vector Agent에서 로그 수신                  │                 │
│  │  → 파싱 / 표준화 / remap                                 │                 │
│  │  → ClickHouse sink (gpu_unified_logs)                    │                 │
│  └──────────────────────────────────────────────────────────┘                  │
│                                                                                │
│  ┌────────────────────────────────┐                                           │
│  │  Metadata Collector             │  ← S2 API + vCenter API 폴링            │
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
│  │ ← 중앙 vmagent             │   │ ← Vector Aggregator (로그)             │ │
│  │   remote_write              │   │ ← Metadata Collector INSERT            │ │
│  │                             │   │ ← Profiling Controller INSERT          │ │
│  │ • L1: GPU HW (NVML 기반)   │   │ ← Custom API INSERT                    │ │
│  │ • L2: GPU Profiling (CUPTI) │   │                                         │ │
│  │   ⚠ L3 실행 시 일시 중단   │   │ • gpu_unified_logs (로그)               │ │
│  │ • L2: 추론 서버 메트릭      │   │ • gpu_demand / gpu_inventory            │ │
│  │ • 시스템/K8s 메트릭         │   │ • gpu_events (Zabbix)                   │ │
│  │                             │   │ • gpu_profiling_traces / sessions       │ │
│  │ 표준 라벨: env / cluster /  │   │ • s2_jobs / s2_nodes                   │ │
│  │ node / gpu / workload_type  │   │ • s2_projects / s2_pools               │ │
│  │                             │   │ • vmware_vm_inventory                   │ │
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
│  │  • 대시보드:                                                            │  │
│  │    - GPU Health (L1) / GPU Efficiency (L2) / Inference SLA (L2)        │  │
│  │    - Training Comm (L2) / Profiling Analysis (L3)                      │  │
│  │    - Demand & Capacity / System Overview                               │  │
│  │    - ★ Job Explorer / ★ VM GPU Inventory                              │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                │
│  ┌──────────────────────────┐  ┌───────────────────────────────────────────┐  │
│  │  vmalert + Alertmanager  │  │  L3 Profiling Controller (온디맨드)       │  │
│  │  • GPU 이상/효율/SLA     │  │  • Module A (PyTorch) / B (Nsight)       │  │
│  │  • Slack/Email 알림       │  │  • ⚠ L2 DCGM Profiling pause/resume     │  │
│  └──────────────────────────┘  └───────────────────────────────────────────┘  │
│  ┌──────────────────────────┐                                                 │
│  │  Custom Ingestion API    │                                                 │
│  └──────────────────────────┘                                                 │
└───────────────────────────────────────────────────────────────────────────────┘
```

**데이터 흐름 요약 (v5 Pull 기반):**

```
경로 A — 메트릭 Pull (중앙 vmagent → 각 노드 Exporter)
  중앙 vmagent이 모든 노드의 HTTP 엔드포인트를 주기적으로 scrape

  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────┐
  │ GPU 노드          │ ←Pull── │ 중앙 vmagent      │ ──write─→│VictoriaMetrics│
  │  :9400 (DCGM)    │         │ (K8s, HA)         │         │              │
  │  :9100 (node_exp) │         │ File SD + K8s SD  │         │              │
  │  :8080 (inference)│         └───────────────────┘         └──────────────┘
  └──────────────────┘
  ※ 노드에 vmagent 없음!

경로 A' — 로그 경량 Push (노드 Vector Agent → 중앙 Vector Aggregator)
  노드의 Vector Agent가 로그를 읽어 중앙으로 forward (파싱 없음)

  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────┐
  │ GPU 노드          │ ──Push─→│ Vector Aggregator │ ──sink──→│ ClickHouse   │
  │  Vector Agent     │         │ (K8s)             │         │              │
  │  (경량, forward만)│         │ (파싱/변환/라우팅)│         │              │
  └──────────────────┘         └───────────────────┘         └──────────────┘

경로 B — 레거시 API 폴링 (Metadata Collector 경유)
  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────┐
  │ S2 API Server    │ ←Poll── │ Metadata          │ ──INSERT→│ ClickHouse   │
  │ vCenter API      │         │ Collector (K8s)   │         │              │
  └──────────────────┘         └───────────────────┘         └──────────────┘
```

---

## 6. SW Stack 구성 (확정 + 권장)

### 6.1 확정 컴포넌트

| 컴포넌트 | 역할 | 배포 방식 | 비고 |
|---|---|---|---|
| **ClickHouse** | 로그/JSON/프로파일링/메타데이터 저장 | K8s: Altinity ClickHouse Operator | Cluster mode 권장 |
| **VictoriaMetrics** | 시계열 메트릭 저장 (L1+L2) | K8s: VictoriaMetrics Operator | Cluster mode |

### 6.2 권장 컴포넌트 - 노드 배포 (Exporter + 경량 Agent)

> **v5 변경**: 노드에서 vmagent 제거. Exporter는 HTTP 노출만, Vector는 경량 forward만 담당.

| 컴포넌트 | 역할 | Baremetal/VM 배포 | K8s 배포 | 비고 |
|---|---|---|---|---|
| **DCGM Exporter** | GPU 메트릭 HTTP 노출 (L1+L2 Profiling) | systemd 서비스 | DaemonSet (GPU 노드) | **Pull 대기 (:9400)** |
| **node_exporter** | 호스트 OS 메트릭 HTTP 노출 | systemd 서비스 | DaemonSet | **Pull 대기 (:9100)** |
| **Vector Agent** | 로그 수집 → 중앙 Aggregator 전송 | systemd 서비스 | DaemonSet | **경량 Push (forward만)** |
| ~~**vmagent**~~ | ~~메트릭 scrape → VM 전송~~ | ❌ 제거 | ❌ 제거 | **v5에서 제거** |

### 6.3 권장 컴포넌트 - 중앙 수집 레이어 (K8s 배포, v5 신규)

> **v5 변경**: 메트릭/로그 수집의 제어권이 중앙으로 이동.

| 컴포넌트 | 역할 | 배포 방식 | 비고 |
|---|---|---|---|
| **vmagent (Central)** | 모든 노드 Exporter를 Pull scrape → VM remote_write | K8s Deployment (HA, 2+ replica) | **v5 핵심, File SD + K8s SD** |
| **Vector Aggregator** | 노드 Vector Agent에서 로그 수신 → 파싱/변환 → ClickHouse | K8s Deployment | **v5 신규** |

### 6.4 권장 컴포넌트 - 추론/K8s 전용

| 컴포넌트 | 역할 | 배포 방식 | 비고 |
|---|---|---|---|
| **추론 서버 /metrics** | 추론 메트릭 HTTP 노출 (vLLM/TGI/Triton) | 추론 서버 자체 기능 | **중앙 vmagent이 Pull** |
| **kube-state-metrics** | K8s 오브젝트 상태 메트릭 | Deployment (1개) | **중앙 vmagent이 Pull** |

### 6.5 권장 컴포넌트 - 서빙/알림 레이어 (K8s 배포)

| 컴포넌트 | 역할 | 배포 방식 | 대안 |
|---|---|---|---|
| **Grafana** | 시각화/대시보드 (VM + ClickHouse 통합) | Helm Chart | - |
| **vmalert** | PromQL 기반 알림 규칙 + L3 트리거 | VictoriaMetrics Operator | - |
| **Alertmanager** | 알림 라우팅 (Slack/Email/PagerDuty) | Helm Chart | - |

### 6.6 권장 컴포넌트 - 메타데이터 수집 (v4~)

| 컴포넌트 | 역할 | 배포 방식 | 비고 |
|---|---|---|---|
| **Metadata Collector** | S2 + VMware 메타데이터 수집 → ClickHouse | K8s Deployment (1 replica) | Adapter 패턴, Python/Go |
| ├─ S2 Adapter | S2 Job/Node 메타데이터 폴링 | Collector 내장 모듈 | REST API 또는 CLI 방식 |
| └─ VMware Adapter | vCenter VM Inventory 폴링 | Collector 내장 모듈 | pyVmomi SDK |

### 6.7 권장 컴포넌트 - L2.5 Job 통계 + L3 프로파일링

| 컴포넌트 | 역할 | 배포 방식 | CUPTI 충돌 | 비고 |
|---|---|---|---|---|
| **DCGM Job Stats** | Job별 GPU 효율 집계 (L2.5) | S2 Hook 연동 | ✅ 없음 | Phase 3, L2와 동시 가능 |
| **Profiling Controller** | L3 요청 관리, **L2 pause/resume** | K8s Deployment | - | L2↔L3 CUPTI 충돌 관리 |
| **Module A: PyTorch Profiler** | 학습 오퍼레이터 분석 | 학습 코드 내 | ⚠ L2 일시 중단 | Phase 5 |
| **Module B: Nsight Systems** | 범용 GPU 타임라인 분석 | GPU 노드에 nsys 설치 | ⚠ L2 일시 중단 | Phase 5, 비침습적 |
| **Result Processor** | 결과 파싱 → ClickHouse | Profiling Controller 내장 | - | - |

> **Module C (CUPTI Wrapper)는 우선순위 하향**: Module B (Nsight Systems)가 비침습적이면서 더 풍부한 데이터를 제공하고, 동일하게 CUPTI를 사용하므로 별도 CUPTI Wrapper 개발의 필요성 감소. 추론 환경에서도 Nsight Systems `--duration=30` attach가 더 실용적.

### 6.8 선택적 컴포넌트

| 컴포넌트 | 역할 | 언제 필요한가 | 비고 |
|---|---|---|---|
| **Kafka (Strimzi Operator)** | 메시지 큐/버퍼 | GPU 클러스터 5개 이상 | 초기에는 없이 시작 |
| **Custom Ingestion API** | GPU 수요 JSON 수집 | 외부 수요 데이터 수집 시 | Go/Python FastAPI |
| **Zabbix Agent** | IPMI/HW/SNMP (역할 축소) | Baremetal HW 모니터링 유지 시 | GPU 수집은 이관 |

---

## 7. 데이터 흐름 상세

### 7.1 L1+L2 메트릭 흐름 — Pull 기반 (v5)

```
  노드 (Exporter HTTP 노출만, Pull 대기)       중앙 (K8s)
  ─────────────────────────────────────       ─────────────────────────

  [Baremetal/VM]                              ┌─────────────────────────┐
   DCGM Exporter (:9400, L1+L2)  ◄── Pull ──│                         │
   node_exporter (:9100)          ◄── Pull ──│  vmagent (Central, HA)  │
   추론 서버 /metrics (:8080)     ◄── Pull ──│                         │
                                              │  File SD:               │
  [K8s]                                       │   baremetal-gpu-*.json  │──→ VictoriaMetrics
   DCGM Exporter (Pod :9400)     ◄── Pull ──│   vm-gpu-*.json         │    Cluster
   node_exporter (Pod :9100)      ◄── Pull ──│                         │      │
   추론 서버 /metrics (Pod)       ◄── Pull ──│  K8s SD:                │  ┌───┴───┐
   kube-state-metrics (:8080)    ◄── Pull ──│   auto-discover Pods    │  │Grafana│
                                              └─────────────────────────┘  └───────┘

  ※ 노드에 vmagent 없음 — Exporter가 HTTP 엔드포인트만 열어놓으면 끝
  ※ 수집 주기/대상/라벨 변경은 중앙 vmagent config 1곳에서 관리
```

### 7.2 로그 흐름 — 경량 Push (v5)

```
  노드 (Vector Agent, 경량)                   중앙 (K8s)
  ──────────────────────────                  ─────────────────────────

  [모든 환경]                                  ┌─────────────────────────┐
   syslog, /var/log/nvidia*   ──→ Vector  ─┐  │                         │
  [K8s]                           Agent    │  │  Vector Aggregator      │
   Pod stdout/stderr          ──→ (경량)  ─┤──→│                         │──→ ClickHouse
  [학습 환경]                     forward  │  │  • 파싱 / remap          │    (gpu_unified_logs)
   NCCL 로그                  ──→  만!    ─┤  │  • 표준 스키마 변환      │      │
  [학습 환경]                              │  │  • ClickHouse sink      │  ┌───┴───┐
   학습 프레임워크 로그       ──→          ─┘  └─────────────────────────┘  │Grafana│
                                                                            └───────┘

  ※ 노드의 Vector Agent는 파싱 없이 원본 로그를 중앙으로 forward만 수행
  ※ 파싱, 변환, 라우팅은 모두 중앙 Vector Aggregator에서 처리
  ※ 왜 로그는 Push인가?
    → 메트릭은 "현재값 스냅샷" → Pull이 자연스러움 (언제 와서 읽어도 최신값)
    → 로그는 "이벤트 스트림" → Push가 필수 (한 번 지나간 로그는 다시 안 옴)
```

### 7.3 레거시 메타데이터 흐름 — 경로 B (v4.1)

```
  S2 API/CLI 서버                          VMware vCenter API
  (Baremetal 환경 소속)                     (VM 환경 소속)
  ┌──────────────────────┐           ┌──────────────────────────┐
  │ S2 Scheduler         │           │ VMware vCenter            │
  │                      │           │                           │
  │ /api/v1/jobs         │           │ pyVmomi:                  │
  │ /api/v1/nodes        │           │   VM + GPU + Host 조회    │
  │ /api/v1/projects     │           │                           │
  │ /api/v1/pools        │           │                           │
  └──────────┬───────────┘           └──────────────┬────────────┘
             │                                      │
             │  API 폴링 (60~600초)                 │  API 폴링 (300초)
             │                                      │
             ▼                                      ▼
       ┌────────────────────────────────────────────────┐
       │           Metadata Collector                     │
       │           (K8s Deployment, 1 replica)            │
       │                                                  │
       │  S2 Adapters:              VMware Adapter:       │
       │   ├ Jobs    (시계열, 60s)   └ VMs (현재값, 300s) │
       │   ├ Nodes   (현재값, 120s)                       │
       │   ├ Projects (스냅샷, 600s)                      │
       │   └ Pools    (스냅샷, 600s)                      │
       └──────────────────────┬───────────────────────────┘
                              │ Batch INSERT
                              ▼
                    ClickHouse Cluster
                  ┌────────────────────────┐
                  │ • s2_jobs     (시계열)  │
                  │ • s2_nodes    (현재값)  │
                  │ • s2_projects (스냅샷)  │
                  │ • s2_pools    (스냅샷)  │
                  │ • vmware_vm_inventory   │
                  └───────────┬────────────┘
                              │
                     ┌────────┴────────┐
                     │     Grafana     │
                     │  Job Explorer   │
                     │  VM Inventory   │
                     │  GPU↔Job 결합   │
                     └─────────────────┘
```

### 7.4 L3 프로파일링 흐름 (온디맨드, ⚠ L2 일시 중단)

```
  수동 API / vmalert 자동 / CronJob
         │
         ▼
  Profiling Controller
    ① DCGM Profiling 일시 중단 (대상 GPU, CUPTI 충돌 방지)
    ② Module A (PyTorch) 또는 Module B (Nsight) 실행
    ③ Result Processor → ClickHouse (gpu_profiling_traces/sessions)
    ④ DCGM Profiling 재개
```

### 7.5 GPU 수요/JSON 데이터 흐름

```
외부 시스템 → Custom Ingestion API → ClickHouse (gpu_demand, gpu_inventory)
```

### 7.6 기존 Zabbix 연동 흐름 (Baremetal 한정)

```
Baremetal → Zabbix Agent → Zabbix Server (IPMI/HW/SNMP 전용)
                              └→ (선택) Webhook → ClickHouse (gpu_events)
```

---

## 8. ClickHouse 테이블 설계 (표준 스키마)

### 8.1 통합 로그 테이블

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

### 8.2 GPU 수요 데이터 테이블

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

### 8.3 GPU 인벤토리 테이블

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

### 8.4 Zabbix 이벤트 테이블 (선택)

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

### 8.5 L3 프로파일링 트레이스/세션 테이블

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

### 8.6 S2 메타데이터 저장 전략 개요 (v4.1 개정)

S2 관련 데이터를 **데이터의 성격에 따라 3가지 저장 전략**으로 분리합니다.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    S2 메타데이터 저장 전략                            │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │ 시계열 (History) │  │ 현재값 (Current)│  │ 스냅샷 (Snapshot)   │ │
│  │ MergeTree        │  │ ReplacingMT     │  │ ReplacingMT         │ │
│  │                  │  │                 │  │                      │ │
│  │ s2_jobs          │  │ s2_nodes        │  │ s2_projects          │ │
│  │                  │  │                 │  │ s2_pools             │ │
│  │ 모든 시점의 상태 │  │ 노드당 최신     │  │ 변경 시에만 갱신     │ │
│  │ 이력을 보관      │  │ 상태 1건만 유지 │  │ 설정값 기록          │ │
│  │                  │  │                 │  │ (JSON 중심)          │ │
│  │ "이 Job이 언제   │  │ "이 노드가 지금 │  │ "이 프로젝트의       │ │
│  │  running이었고   │  │  idle인지       │  │  현재 FairShare가    │ │
│  │  언제 끝났는지"  │  │  down인지"      │  │  얼마인지"           │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘ │
│                                                                      │
│  왜 이렇게 나누는가?                                                │
│  • Jobs: 상태 변화(pending→running→completed)의 시간축 분석이 중요  │
│    → "이 Job이 언제 큐에 들어갔고, 대기 시간이 얼마였는지" 추적     │
│  • Nodes: 현재 상태만 중요, 이력은 s2_jobs에서 간접 추적 가능       │
│    → FINAL 쿼리로 노드당 최신 1건만 빠르게 조회                     │
│  • Projects/Pools: 설정 변경이 드물고, 변경 시점의 스냅샷만 보관    │
│    → JSON으로 유연하게 구조 표현, 변경 이력 최소한으로 유지          │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.7 S2 Job 메타데이터 테이블 — 시계열 (v4.1)

**저장 전략: 시계열 (MergeTree)** — 모든 폴링 시점의 상태를 보관하여, Job의 라이프사이클을 시간축으로 분석할 수 있습니다.

```sql
CREATE TABLE s2_jobs (
    -- 수집 시점 (시계열의 시간축)
    collected_at     DateTime64(3),         -- Metadata Collector 폴링 시각

    -- Job 식별
    job_id           String,                -- S2 Job 고유 ID
    job_name         String,                -- Job 이름 (사용자 지정)

    -- 사용자/조직 정보
    user_id          LowCardinality(String),  -- 제출자 ID
    team             LowCardinality(String),  -- 팀/그룹
    project          LowCardinality(String),  -- S2 Project 이름 (FairShare 단위)
    queue            LowCardinality(String),  -- S2 Queue (파티션) 이름

    -- Job 상태 (시계열로 추적되는 핵심 필드)
    status           LowCardinality(String),  -- pending, running, completed, failed, cancelled

    -- 시간 정보
    submit_time      Nullable(DateTime64(3)), -- 제출 시각
    start_time       Nullable(DateTime64(3)), -- 실행 시작 시각
    end_time         Nullable(DateTime64(3)), -- 완료 시각

    -- 리소스 할당 (GPU 메트릭 결합의 핵심)
    node_list        Array(String),           -- ['gpu-node-03', 'gpu-node-04']
    gpu_count        UInt16,                  -- 할당된 총 GPU 수
    gpu_indices      Array(UInt8),            -- [0, 1, 2, 3] (노드별 GPU 인덱스)
    cpu_count        UInt16,
    memory_mb        UInt32,

    -- 완료 정보
    exit_code        Nullable(Int32),

    -- 확장 데이터
    metadata         String                   -- JSON: 커맨드, 환경변수, 의존성 등
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(collected_at)
ORDER BY (job_id, collected_at)
TTL collected_at + INTERVAL 6 MONTH;
```

**시계열로 관리하면 좋은 이유:**

```
60초마다 폴링하면 Job #84723의 이력이 이렇게 쌓임:

collected_at          | status  | gpu_count | node_list          | ...
─────────────────────┼─────────┼───────────┼────────────────────┼────
2025-07-15 09:00:12  | pending |     0     | []                 |
2025-07-15 09:01:12  | pending |     0     | []                 |  ← 대기 2분
2025-07-15 09:02:15  | running |     4     | ['gpu-node-03']    |  ← 할당됨
2025-07-15 09:03:15  | running |     4     | ['gpu-node-03']    |
...
2025-07-15 21:00:45  | completed|    4     | ['gpu-node-03']    |  ← 12시간 학습

이력이 있으면:
  ✅ Job 대기 시간 (pending → running 전환 시점 차이)
  ✅ Job 실행 시간 (running → completed 전환 시점 차이)
  ✅ 특정 시각에 어떤 GPU를 사용하고 있었는지 (과거 시점 회고)
  ✅ GPU Util 시계열과 시간축 정렬하여 "이 시간대에 어떤 Job이 이 GPU를 썼는지" 확인
```

**주요 쿼리 예시:**

```sql
-- 1) 현재 실행 중인 Job 목록 (최신 스냅샷)
SELECT job_id, job_name, user_id, team, project, queue,
       node_list, gpu_count, gpu_indices, start_time
FROM s2_jobs
WHERE status = 'running'
  AND collected_at = (
    SELECT max(collected_at) FROM s2_jobs AS inner
    WHERE inner.job_id = s2_jobs.job_id
  )
ORDER BY start_time;

-- 2) 특정 노드+GPU에서 실행 중인 Job 찾기 (GPU 메트릭 Enrichment 핵심)
SELECT job_id, job_name, user_id, team
FROM s2_jobs
WHERE has(node_list, 'gpu-node-03')
  AND has(gpu_indices, 0)
  AND status = 'running'
  AND collected_at >= now() - INTERVAL 2 MINUTE
ORDER BY collected_at DESC
LIMIT 1;

-- 3) Job 대기 시간 분석 (시계열 이력 활용)
SELECT job_id, job_name, team, queue,
       min(collected_at) AS first_seen_pending,
       minIf(collected_at, status = 'running') AS first_seen_running,
       dateDiff('second', min(collected_at),
                minIf(collected_at, status = 'running')) AS wait_seconds
FROM s2_jobs
WHERE collected_at >= now() - INTERVAL 24 HOUR
GROUP BY job_id, job_name, team, queue
HAVING minIf(collected_at, status = 'running') IS NOT NULL
ORDER BY wait_seconds DESC;

-- 4) 특정 시각에 어떤 Job이 어떤 GPU를 사용하고 있었는지 (과거 회고)
SELECT job_id, job_name, user_id, team, node_list, gpu_indices, gpu_count
FROM s2_jobs
WHERE status = 'running'
  AND collected_at >= '2025-07-15 14:00:00'
  AND collected_at <  '2025-07-15 14:02:00'
ORDER BY job_id;

-- 5) 팀별 GPU 사용 시간 (GPU-Hours) 계산
SELECT team,
       countDistinct(job_id) AS job_count,
       sum(gpu_count) * 60 / 3600 AS gpu_hours  -- 60초 간격 × GPU 수
FROM s2_jobs
WHERE status = 'running'
  AND collected_at >= now() - INTERVAL 24 HOUR
GROUP BY team
ORDER BY gpu_hours DESC;
```

### 8.8 S2 Node 상태 테이블 — 현재값 (v4.1)

**저장 전략: 현재값 (ReplacingMergeTree)** — 노드당 최신 상태 1건만 유지합니다. 과거 이력이 필요할 때는 s2_jobs 테이블에서 간접 추적합니다.

```sql
CREATE TABLE s2_nodes (
    -- 수집 시점 (ReplacingMergeTree의 version 컬럼)
    collected_at     DateTime64(3),

    -- 노드 식별 (ORDER BY 키 = 유니크 키)
    node_id          String,                  -- 노드 호스트명

    -- 클러스터/Pool 소속
    cluster_id       LowCardinality(String),  -- S2 클러스터 식별자
    pool             LowCardinality(String),  -- Logical Node Pool 이름

    -- 상태
    state            LowCardinality(String),  -- idle, alloc, mixed, drain, down
    partition        LowCardinality(String),  -- S2 파티션 이름

    -- 리소스 정보 (현재값)
    gpu_total        UInt8,
    gpu_alloc        UInt8,                   -- 현재 할당된 GPU 수
    gpu_type         LowCardinality(String),  -- GPU 모델명
    cpu_total        UInt16,
    cpu_alloc        UInt16,
    memory_total_mb  UInt32,
    memory_alloc_mb  UInt32,

    -- 추가 정보
    reason           String,                  -- drain/down 사유 (정상이면 빈 문자열)
    metadata         String                   -- JSON: 추가 정보
) ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (node_id);
```

**ReplacingMergeTree를 쓰는 이유:**

```
노드 120대 × 120초마다 = 매일 86,400 행 → 불필요한 이력이 빠르게 쌓임

ReplacingMergeTree:
  node_id가 ORDER BY 키 → 같은 노드의 이전 행은 자동 제거됨
  FINAL 쿼리로 항상 최신 상태만 조회 (120대 → 120행)
  
  노드 상태 이력이 필요한 경우:
  → s2_jobs 테이블에서 "이 노드에 할당된 Job 변화"로 간접 추적 가능
  → drain/down 이력만 필요하면 gpu_events 테이블에 별도 이벤트로 기록 가능
```

**주요 쿼리 예시:**

```sql
-- 클러스터 전체 GPU 가용률 (현재 상태)
SELECT cluster_id,
       sum(gpu_total) AS total_gpus,
       sum(gpu_alloc) AS allocated_gpus,
       round(sum(gpu_alloc) / sum(gpu_total) * 100, 1) AS utilization_pct,
       countIf(state = 'down') AS down_nodes,
       countIf(state = 'drain') AS drain_nodes
FROM s2_nodes FINAL;

-- Pool별 GPU 가용 현황
SELECT pool, gpu_type,
       count() AS node_count,
       sum(gpu_total) AS total_gpus,
       sum(gpu_total - gpu_alloc) AS available_gpus
FROM s2_nodes FINAL
WHERE state NOT IN ('down', 'drain')
GROUP BY pool, gpu_type;

-- 장애/유지보수 노드 목록
SELECT node_id, state, reason, pool, collected_at
FROM s2_nodes FINAL
WHERE state IN ('down', 'drain')
ORDER BY collected_at;
```

### 8.9 S2 Project 정보 테이블 — 스냅샷 (v4.1 신규)

**저장 전략: 스냅샷 (ReplacingMergeTree)** — Project 설정은 변경 빈도가 낮으므로, 변경 시점의 스냅샷만 보관합니다. JSON 중심으로 유연한 구조를 가집니다.

```sql
CREATE TABLE s2_projects (
    -- 수집 시점 (version 컬럼)
    collected_at     DateTime64(3),

    -- Project 식별 (ORDER BY 키)
    project_id       String,                  -- S2 Project 고유 ID/이름
    cluster_id       LowCardinality(String),  -- S2 클러스터 식별자

    -- 기본 정보
    project_name     String,                  -- Project 표시 이름
    description      String,                  -- 설명
    owner            LowCardinality(String),  -- 프로젝트 책임자/팀
    status           LowCardinality(String),  -- active, suspended, archived

    -- FairShare / Limit / License (JSON으로 유연하게 관리)
    fairshare_config String,                  -- JSON: FairShare 설정 전체
    -- 예: {
    --   "weight": 100,
    --   "max_share": 0.3,
    --   "priority": "normal",
    --   "preemptable": true
    -- }

    resource_limits  String,                  -- JSON: 리소스 제한 설정
    -- 예: {
    --   "max_gpus": 64,
    --   "max_jobs": 20,
    --   "max_running_jobs": 10,
    --   "max_gpus_per_job": 16,
    --   "max_walltime_hours": 168,
    --   "allowed_queues": ["default", "high-priority"],
    --   "allowed_pools": ["pool-a100", "pool-h100"]
    -- }

    license_config   String,                  -- JSON: 라이선스 정보
    -- 예: {
    --   "sw_licenses": {
    --     "cuda_toolkit": {"type": "site", "count": -1},
    --     "nccl": {"type": "site", "count": -1}
    --   },
    --   "feature_flags": ["multi_node", "preemption"]
    -- }

    -- 전체 원본 (S2 API 응답 그대로)
    raw_config       String                   -- JSON: S2 API 원본 응답 전체
) ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (project_id, cluster_id);
```

**주요 쿼리 예시:**

```sql
-- 전체 Project FairShare 현황
SELECT project_id, project_name, owner,
       JSONExtractInt(fairshare_config, 'weight') AS weight,
       JSONExtractFloat(fairshare_config, 'max_share') AS max_share,
       JSONExtractInt(resource_limits, 'max_gpus') AS max_gpus,
       JSONExtractInt(resource_limits, 'max_running_jobs') AS max_running_jobs
FROM s2_projects FINAL
WHERE status = 'active'
ORDER BY weight DESC;

-- FairShare 대비 실제 GPU 사용률 (s2_jobs와 결합)
SELECT p.project_id, p.project_name,
       JSONExtractInt(p.resource_limits, 'max_gpus') AS limit_gpus,
       count(DISTINCT j.job_id) AS running_jobs,
       sum(j.gpu_count) AS current_gpus
FROM s2_projects FINAL AS p
LEFT JOIN (
    SELECT project, job_id, gpu_count
    FROM s2_jobs
    WHERE status = 'running'
      AND collected_at >= now() - INTERVAL 2 MINUTE
) AS j ON p.project_id = j.project
GROUP BY p.project_id, p.project_name, p.resource_limits;
```

### 8.10 S2 Pool 정보 테이블 — 스냅샷 (v4.1 신규)

**저장 전략: 스냅샷 (ReplacingMergeTree)** — Pool 구성(어떤 노드가 어떤 Logical Pool에 속하는지)은 변경이 드물므로 스냅샷으로 관리합니다.

```sql
CREATE TABLE s2_pools (
    -- 수집 시점 (version 컬럼)
    collected_at     DateTime64(3),

    -- Pool 식별 (ORDER BY 키)
    pool_id          String,                  -- Logical Node Pool 이름/ID
    cluster_id       LowCardinality(String),  -- S2 클러스터 식별자

    -- 기본 정보
    pool_name        String,                  -- Pool 표시 이름
    description      String,
    status           LowCardinality(String),  -- active, maintenance, disabled

    -- Pool 구성 (JSON으로 유연하게 관리)
    node_list        String,                  -- JSON: Pool에 속한 노드 목록
    -- 예: {
    --   "nodes": ["gpu-node-01", "gpu-node-02", ..., "gpu-node-16"],
    --   "count": 16
    -- }

    gpu_config       String,                  -- JSON: Pool의 GPU 구성 정보
    -- 예: {
    --   "gpu_type": "H100",
    --   "gpus_per_node": 8,
    --   "total_gpus": 128,
    --   "interconnect": "NVLink",
    --   "network": "InfiniBand NDR"
    -- }

    scheduling_policy String,                 -- JSON: 스케줄링 정책
    -- 예: {
    --   "default_queue": "default",
    --   "allowed_projects": ["proj-a", "proj-b"],
    --   "exclusive_mode": true,
    --   "preemption_enabled": true,
    --   "max_job_walltime_hours": 168
    -- }

    -- 전체 원본
    raw_config       String                   -- JSON: S2 API 원본 응답 전체
) ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (pool_id, cluster_id);
```

**주요 쿼리 예시:**

```sql
-- 전체 Pool 현황 (노드 수, GPU 수)
SELECT pool_id, pool_name,
       JSONExtractString(gpu_config, 'gpu_type') AS gpu_type,
       JSONExtractInt(gpu_config, 'total_gpus') AS total_gpus,
       JSONLength(JSONExtractRaw(node_list, 'nodes')) AS node_count,
       status
FROM s2_pools FINAL
WHERE status = 'active';

-- Pool별 실제 사용률 (s2_nodes와 결합)
SELECT p.pool_id, p.pool_name,
       JSONExtractInt(p.gpu_config, 'total_gpus') AS total_gpus,
       sum(n.gpu_alloc) AS allocated_gpus,
       round(sum(n.gpu_alloc) / JSONExtractInt(p.gpu_config, 'total_gpus') * 100, 1) AS util_pct
FROM s2_pools FINAL AS p
JOIN s2_nodes FINAL AS n ON n.pool = p.pool_id
GROUP BY p.pool_id, p.pool_name, p.gpu_config;

-- 특정 Pool에 속한 노드 상세
SELECT n.node_id, n.state, n.gpu_total, n.gpu_alloc, n.gpu_type
FROM s2_nodes FINAL AS n
WHERE n.pool = 'pool-h100-cluster-a'
ORDER BY n.node_id;
```

### 8.11 VMware VM Inventory 테이블 (v4)

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

### 8.12 테이블 요약

| # | 테이블 | 엔진 | 저장 전략 | 데이터 소스 | TTL | 비고 |
|---|---|---|---|---|---|---|
| 1 | `gpu_unified_logs` | MergeTree | 시계열 | Vector | 6개월 | 모든 환경 통합 로그 |
| 2 | `gpu_demand` | MergeTree | 시계열 | Ingestion API | - | 수요 분석 |
| 3 | `gpu_inventory` | ReplacingMT | 현재값 | Ingestion API | - | GPU 자산 |
| 4 | `gpu_events` | MergeTree | 시계열 | Zabbix Webhook | 1년 | HW 이벤트 |
| 5 | `gpu_profiling_traces` | MergeTree | 시계열 | Profiling Controller | 3개월 | L3 커널 데이터 |
| 6 | `gpu_profiling_sessions` | ReplacingMT | 현재값 | Profiling Controller | - | L3 세션 요약 |
| 7 | **`s2_jobs`** | **MergeTree** | **시계열** | Metadata Collector | 6개월 | **Job 라이프사이클 이력** |
| 8 | **`s2_nodes`** | **ReplacingMT** | **현재값** | Metadata Collector | - | **노드 최신 상태** |
| 9 | **`s2_projects`** | **ReplacingMT** | **스냅샷** | Metadata Collector | - | **FairShare/Limit/License (JSON)** |
| 10 | **`s2_pools`** | **ReplacingMT** | **스냅샷** | Metadata Collector | - | **Logical Node Pool 구성 (JSON)** |
| 11 | **`vmware_vm_inventory`** | ReplacingMT | 현재값 | Metadata Collector | 12개월 | VMware GPU VM |

---

## 9. K8s 배포 전략

### 9.1 Namespace 구조

```
monitoring/                        # 메트릭 수집/저장/알림
  ├── victoriametrics-*            # VM 클러스터
  ├── vmagent-central-*            # ★ v5: 중앙 Pull scraper (HA)
  ├── vmalert-*                    # 알림 규칙 엔진
  └── alertmanager-*               # 알림 라우팅

clickhouse/                        # ClickHouse 전용
  ├── clickhouse-operator
  └── clickhouse-cluster-*

logging/                           # 로그 수집
  ├── vector-aggregator-*          # ★ v5: 중앙 로그 수신/파싱/적재
  └── vector-agent-*               # ★ v5: K8s 노드의 경량 Agent (DaemonSet)

visualization/                     # 시각화
  └── grafana-*

profiling/                         # L3 프로파일링
  ├── profiling-controller-*
  └── profiling-cronjob-*

metadata/                          # 메타데이터 수집
  └── metadata-collector-*         # Metadata Collector Deployment

ingestion/                         # 커스텀 데이터 수집 (선택)
  └── gpu-ingestion-api-*
```

### 9.2 배포 도구 및 방법

| 컴포넌트 | K8s 내부 | Baremetal/VM | 비고 |
|---|---|---|---|
| VictoriaMetrics | VictoriaMetrics Operator (Helm) | - | K8s에만 배포 |
| ClickHouse | Altinity ClickHouse Operator (Helm) | - | K8s에만 배포 |
| Grafana | Helm Chart | - | K8s에만 배포 |
| vmalert | VictoriaMetrics Operator | - | K8s에만 배포 |
| Alertmanager | Helm Chart | - | K8s에만 배포 |
| **vmagent (Central)** *(v5)* | **Deployment (HA, 2+ replica)** | - | **K8s에만, File SD ConfigMap** |
| **Vector Aggregator** *(v5)* | **Deployment** | - | **K8s에만, 로그 수신/파싱** |
| Profiling Controller | Deployment + Service | - | K8s에만 배포 |
| Metadata Collector | Deployment (1 replica) | - | K8s에만, ConfigMap + Secret |
| DCGM Exporter | DaemonSet (GPU 노드) | systemd (Ansible) | **Pull 대기 (:9400)** |
| node_exporter | DaemonSet (모든 노드) | systemd (Ansible) | **Pull 대기 (:9100)** |
| **Vector Agent** *(v5)* | DaemonSet | systemd (Ansible) | **경량, forward만** |
| ~~vmagent~~ | ~~DaemonSet~~ | ~~systemd~~ | **v5에서 제거 (노드)** |
| kube-state-metrics | Deployment (1개) | - | K8s 전용 |

### 9.3 리소스 가이드라인 (초기 규모)

> 기준: GPU 노드 50~100대 (Baremetal + K8s + VM 합산), 메트릭 수집 주기 15초

**K8s 내부 (중앙 수집/스토리지/서빙 컴포넌트):**

| 컴포넌트 | CPU Request | Memory Request | Storage | 비고 |
|---|---|---|---|---|
| VictoriaMetrics vmstorage (x2) | 2 core | 4Gi | 500Gi SSD (PVC) | |
| VictoriaMetrics vminsert (x2) | 1 core | 2Gi | - | |
| VictoriaMetrics vmselect (x2) | 1 core | 2Gi | - | |
| ClickHouse (x2 shard, x2 replica) | 4 core | 16Gi | 1Ti SSD (PVC) | |
| Grafana | 0.5 core | 512Mi | 10Gi | |
| vmalert | 0.25 core | 256Mi | - | |
| Alertmanager | 0.25 core | 256Mi | - | |
| **vmagent Central (x2 HA)** *(v5)* | **1 core** | **1Gi** | **-** | **100대 노드 Pull scrape** |
| **Vector Aggregator** *(v5)* | **1 core** | **1Gi** | **-** | **로그 수신/파싱/CH sink** |
| Profiling Controller | 0.5 core | 512Mi | 50Gi | |
| Metadata Collector | 0.25 core | 256Mi | - | |

**각 GPU 노드별 에이전트 (v5, Baremetal/K8s/VM 공통):**

| 에이전트 | CPU | Memory | v4 대비 | 비고 |
|---|---|---|---|---|
| DCGM Exporter (L1+L2) | 0.10 core | 128Mi | 동일 | Pull 대기만 |
| node_exporter | 0.05 core | 64Mi | 동일 | Pull 대기만 |
| Vector Agent (경량) | 0.10 core | 128Mi | **-50%** | forward만, 파싱 없음 |
| ~~vmagent~~ | ~~0.25 core~~ | ~~256Mi~~ | **제거** | v5에서 제거 |
| **노드당 합계** | **~0.25 core** | **~320Mi** | **-57%** | v4: ~0.75 core, ~768Mi |

> **리소스 Trade-off**: 노드당 리소스가 절반 이하로 줄어든 대신, 중앙 vmagent/Vector Aggregator에 리소스 집중. 총량은 비슷하지만 GPU 노드의 워크로드 간섭이 크게 감소.

---

## 10. 단계별 구축 로드맵

### Phase 1: Foundation - L1+L2 메트릭 파이프라인 (2~3주)

**목표**: 모든 환경에서 GPU 메트릭(L1+L2)이 중앙 Pull 방식으로 VictoriaMetrics에 저장되고 Grafana에서 조회

- [ ] K8s 클러스터 준비 (네임스페이스, 스토리지 클래스, RBAC)
- [ ] VictoriaMetrics Operator 설치 및 VMCluster 배포
- [ ] DCGM Exporter 커스텀 카운터 CSV 작성 (L1 + L2 Profiling 활성화)
- [ ] K8s 환경: DCGM Exporter + node_exporter DaemonSet 배포 (**vmagent 없음**)
- [ ] Baremetal 환경: DCGM Exporter + node_exporter systemd 설치 (Ansible) (**vmagent 없음**)
- [ ] **중앙 vmagent (K8s Deployment, HA) 배포 (v5)**
  - [ ] File SD 타겟 파일 작성 (Baremetal/VM 노드 목록)
  - [ ] K8s SD 설정 (K8s Pod 자동 검색)
  - [ ] 표준 라벨 적용 (env, cluster, node, gpu, gpu_model, workload_type)
  - [ ] remote_write → VictoriaMetrics 연결
- [ ] **방화벽 확인**: 중앙 vmagent → 각 노드 :9400/:9100 접근 가능 확인
- [ ] Grafana 배포 + VictoriaMetrics datasource 연결
- [ ] GPU Health (L1) + GPU Efficiency (L2) 대시보드 구성
- [ ] **vmagent /targets UI에서 모든 노드 up 상태 확인**

**완료 기준**: Grafana에서 `env` 드롭다운으로 전환하며 GPU Util + Tensor Core Active + DRAM Active 확인 가능, vmagent targets 전체 up

### Phase 2: Log Pipeline + 추론 메트릭 (2~3주)

**목표**: 로그 경량 Push 파이프라인 구축 + 추론 서버 메트릭 Pull 수집

- [ ] ClickHouse Operator 설치 및 클러스터 배포
- [ ] gpu_unified_logs 테이블 생성
- [ ] **Vector Aggregator (K8s Deployment) 배포** — 파싱/변환/ClickHouse sink
- [ ] **K8s: Vector Agent DaemonSet 배포** — 경량 forward만
- [ ] **Baremetal: Vector Agent systemd 설치** (Ansible) — 경량 forward만
- [ ] 추론 서버 /metrics를 **중앙 vmagent의 File SD에 추가** (Pull)
- [ ] NCCL 로그 수집 파이프라인 구성 (Vector Agent → Aggregator)
- [ ] Grafana에 ClickHouse datasource 추가 + 로그 대시보드
- [ ] Inference SLA 대시보드 + Training Communication 대시보드

**완료 기준**: 추론 서버 TTFT/KV Cache가 Grafana에서 실시간 확인 가능, 로그가 Vector Aggregator를 통해 ClickHouse에 적재 확인

### Phase 3: Analytics & Legacy Metadata Integration (3~4주) ← v4 확장

**목표**: 수요 데이터/인벤토리 적재 + **레거시 메타데이터(S2, VMware) 수집 및 GPU 메트릭 결합** + **DCGM Job Stats 연동**

- [ ] gpu_demand, gpu_inventory, gpu_events 테이블 생성
- [ ] **s2_jobs, s2_nodes, s2_projects, s2_pools, vmware_vm_inventory 테이블 생성 (v4.1)**
- [ ] **Metadata Collector 개발 (v4)**
  - [ ] S2 Jobs Adapter (시계열), S2 Nodes Adapter (현재값)
  - [ ] S2 Projects/Pools Adapter (스냅샷, JSON)
  - [ ] VMware Adapter 구현 (pyVmomi)
  - [ ] Schema Normalizer + ClickHouse Writer
  - [ ] K8s Deployment + ConfigMap + Secret 매니페스트
- [ ] **Metadata Collector 배포 및 데이터 수집 검증**
- [ ] **DCGM Job Stats 연동 (L2.5, CUPTI 충돌 없음)**
  - [ ] S2 Job lifecycle hook 설계 (Job 시작 → dcgmi stats -s / Job 종료 → dcgmi stats -x)
  - [ ] Job Stats 결과 → ClickHouse 적재 (gpu_profiling_sessions 테이블 활용)
  - [ ] Job별 GPU 효율 요약 대시보드 패널
- [ ] GPU 수요 데이터 Ingestion API 개발 및 배포
- [ ] (선택) Zabbix Webhook → gpu_events 연동
- [ ] VM 환경 에이전트 배포
- [ ] **Job Explorer 대시보드 구축**: S2 Job↔GPU 메트릭 결합 + DCGM Job Stats
- [ ] **VM GPU Inventory 대시보드 구축**: VMware VM 현황

**완료 기준**: Grafana에서 "gpu-node-03의 GPU 0"에서 실행 중인 S2 Job 정보 확인 가능, Job별 GPU Util 요약 확인 가능, VMware GPU VM 인벤토리 조회 가능

### Phase 4: Alerting & Hardening (1~2주)

**목표**: 알림 시스템 가동, 데이터 보관/백업 정책

- [ ] vmalert L1/L2 규칙 작성
- [ ] vmalert 추론 SLA 규칙 작성
- [ ] Alertmanager 연동 (Slack/Email)
- [ ] ClickHouse TTL 및 파티션 관리
- [ ] VictoriaMetrics retention/downsampling
- [ ] 백업 전략

**완료 기준**: L1/L2 알림 동작 확인, 데이터 보관 정책 적용

### Phase 5: L3 모듈러 프로파일링 시스템 (3~4주)

**목표**: 온디맨드 L3 프로파일링 + 자동 트리거 + **CUPTI 충돌 관리**

- [ ] gpu_profiling_traces, gpu_profiling_sessions 테이블 생성
- [ ] Profiling Controller 개발
  - [ ] REST API + 모듈 관리
  - [ ] **L2 DCGM Profiling pause/resume 기능** (CUPTI 충돌 관리)
  - [ ] DCGM API 연동: `dcgmi profile --pause/--resume` 또는 카운터 CSV 동적 교체
- [ ] Module A (PyTorch Profiler) / Module B (Nsight Systems) 구현
  - [ ] ※ Module C (CUPTI Wrapper)는 우선순위 하향 — Module B로 대체
- [ ] Result Processor → ClickHouse 적재
- [ ] vmalert → Profiling Controller 자동 트리거
- [ ] Grafana Profiling Analysis 대시보드 (L2 gap 표시 포함)

**완료 기준**: REST API로 수동 프로파일링 시 L2 자동 pause/resume 동작, vmalert 자동 트리거 동작

### Phase 6: 고도화 (Ongoing)

- [ ] **Metric Enrichment via vmagent (v4)**: Metadata Collector가 /metrics로 GPU↔Job 매핑 노출, vmagent가 DCGM 메트릭에 job_id 라벨 동적 추가
- [ ] **Metadata Collector 추가 Adapter (v4)**: LDAP/AD (사용자→조직 매핑), CMDB 등
- [ ] Kafka 도입 검토
- [ ] GPU Goodput 메트릭 커스텀 수집기
- [ ] 자동화된 GPU Health & Efficiency Report
- [ ] RL 기반 스케줄러 데이터 연동
- [ ] 추가 환경 온보딩 자동화
- [ ] 프로파일링 결과 기반 자동 최적화 권고

---

## 11. Grafana 대시보드 구성안

| 대시보드 | 데이터 소스 | 메트릭 레벨 | 주요 패널 |
|---|---|---|---|
| **GPU Health** | VictoriaMetrics | L1 | GPU Util, Memory, Temp, Power, Xid 에러 (환경별 필터) |
| **GPU Efficiency** | VictoriaMetrics | L2 | Tensor Active, SM Occupancy, DRAM Active, NVLink BW |
| **Inference SLA** | VictoriaMetrics | L2 | TTFT (P50/P99), TPOT, KV Cache Util, Queue Length, Batch Size |
| **Training Communication** | ClickHouse | L2 | NCCL AllReduce 시간, 통신/연산 비율, NVLink 활용 |
| **Profiling Analysis** | ClickHouse | L3 | 세션별 커널 분석, Top-K 커널, Compute Ratio, 세션 비교 |
| **Demand & Capacity** | ClickHouse | - | GPU 수요 트렌드, 팀별 사용량, 인벤토리 현황 |
| **System Overview** | VictoriaMetrics | - | 환경별 노드 상태, CPU/Mem/Disk/Network |
| ★ **Job Explorer** *(v4)* | **ClickHouse + VM** | - | **S2 Job↔GPU 매핑, 팀별/사용자별 GPU 사용률, Job 대기 시간, Queue 현황** |
| ★ **VM GPU Inventory** *(v4)* | **ClickHouse** | - | **VMware GPU VM 목록, ESXi Host별 분포, Resource Pool별 할당, VM 상태 이력** |

### Job Explorer 대시보드 상세 (v4)

| 패널 | 데이터 소스 | 설명 |
|---|---|---|
| Running Jobs 테이블 | ClickHouse (s2_jobs) | Job ID, 이름, 사용자, 팀, 노드, GPU수, 경과 시간 |
| Job별 GPU Util Heatmap | VM + ClickHouse Join | 각 Job이 사용하는 GPU들의 평균 Util |
| 팀별 GPU 점유 Bar Chart | ClickHouse (s2_jobs) | 팀별 현재 GPU 할당 수 |
| Queue 대기 Job 수 | ClickHouse (s2_jobs) | pending 상태 Job 수 by queue |
| 노드 상태 Map | ClickHouse (s2_nodes) | idle/alloc/drain/down 시각화 |
| Job 이력 Timeline | ClickHouse (s2_jobs) | 특정 노드/GPU의 Job 실행 이력 |

### VM GPU Inventory 대시보드 상세 (v4)

| 패널 | 데이터 소스 | 설명 |
|---|---|---|
| GPU VM 목록 테이블 | ClickHouse (vmware_vm_inventory) | VM명, Host, GPU 종류, 수, 상태, Resource Pool |
| ESXi Host별 GPU 분포 | ClickHouse (vmware_vm_inventory) | 호스트별 GPU VM 수, GPU 종류 |
| Resource Pool별 할당 Pie | ClickHouse (vmware_vm_inventory) | 팀/프로젝트별 GPU 할당 비율 |
| VM GPU Util (결합) | VM + ClickHouse Join | VM의 GPU Util 매핑 (VM 내부 DCGM 수집 시) |

---

## 12. 핵심 설계 결정 사항

| # | 결정 사항 | 선택지 | 권장 | 이유 |
|---|---|---|---|---|
| D1 | 메트릭 수집기 | Prometheus vs **vmagent** | vmagent | VM 에코시스템 통일, 경량. **v5: 중앙 배포 (Pull)** |
| D2 | 로그 수집기 | Fluent Bit vs **Vector** | Vector | ClickHouse sink 네이티브, remap 강력. **v5: Agent/Aggregator 분리** |
| D3 | Exporter 표준화 | 환경별 다르게 vs **동일** | 동일 | 표준 스키마 보장, 운영 복잡도 감소 |
| D4 | DCGM Profiling 활성화 | L1만 vs **L1+L2** | L1+L2 | 1~3% 오버헤드로 효율 상시 분석 |
| D5 | 추론 메트릭 소스 | DCGM만 vs **DCGM+추론서버** | DCGM+추론서버 | TTFT/KV Cache는 추론 서버만 제공 |
| D6 | L3 프로파일링 방식 | 상시 vs **온디맨드 모듈러** | 온디맨드 모듈러 | 오버헤드 관리, 선택적 모듈 |
| D7 | Kafka 필요 여부 | 도입 vs 미도입 | **초기 미도입** | 초기 규모에서는 직접 연결 충분 |
| D8 | ClickHouse 모드 | 단일 vs 클러스터 | **상황별** | 초기 단일 → 규모 커지면 클러스터 |
| D9 | VM 모드 | 단일 vs 클러스터 | **클러스터** | GPU+추론 메트릭 양 많음 |
| D10 | Zabbix 역할 | 전면 유지 vs **역할 축소** | 역할 축소 | IPMI/HW/SNMP만 유지 |
| D11 | Baremetal 배포 | 수동 vs 자동화 | **Ansible** | 일관된 config, 업데이트 용이 |
| **D12** | **메타데이터 수집 방식** *(v4)* | **에이전트 기반 vs 중앙 폴링** | **중앙 폴링 (Metadata Collector)** | **노드에 에이전트 추가 없이, 1개 Deployment로 전체 메타데이터 수집. S2/vCenter는 API 서버가 있으므로 폴링이 적합** |
| **D13** | **GPU↔Job 결합 방식** *(v4)* | **vmagent 라벨 주입 vs Grafana 쿼리 시점 JOIN** | **Grafana JOIN (Phase 3), vmagent 라벨 (Phase 6)** | **Grafana JOIN이 구현 간단하고 충분한 실시간성. 중앙 vmagent Pull 구조에서 라벨 주입도 용이 (Phase 6)** |
| **D14** | **VMware SDK** *(v4)* | **pyVmomi vs govmomi vs REST API** | **pyVmomi (Python)** | **공식 SDK, GPU PCI 디바이스 조회 용이, Metadata Collector Python 구현 시 자연스러움** |
| **D15** | **DCGM Profiling ↔ CUPTI 충돌** *(v4.1)* | **L2 상시 유지 vs L2 일시 중단 vs L3 전용 노드** | **L2 일시 중단 프로토콜 + DCGM Job Stats 병행** | **DCGM Profiling(L2)과 CUPTI 기반 L3는 동시 사용 불가. Phase 3에서 DCGM Job Stats(충돌 없음)로 Job 수준 통계 확보, Phase 5에서 L3 실행 시 대상 GPU의 L2만 일시 중단** |
| **D16** | **S2 메타데이터 저장 전략** *(v4.1)* | **단일 전략 vs 데이터별 분리** | **3가지 전략 분리** | **Jobs: 시계열(MergeTree)로 라이프사이클 추적, Nodes: 현재값(ReplacingMT)으로 최신만, Projects/Pools: 스냅샷(ReplacingMT, JSON)으로 설정 변경 기록** |
| **D17** | **Module C (CUPTI Wrapper) 필요성** *(v4.1)* | **개발 vs 보류** | **우선순위 하향** | **Module B(Nsight Systems)가 비침습적이면서 더 풍부한 데이터 제공. 동일하게 CUPTI 사용하므로 별도 CUPTI Wrapper 개발 이점 적음** |
| **D18** | **메트릭 수집 방식** *(v5)* | **Push (노드 vmagent) vs Pull (중앙 vmagent) vs Hybrid** | **Hybrid Pull** | **메트릭: 중앙 vmagent가 Pull (노드 vmagent 제거, 노드 리소스 57% 절감, 중앙 1곳에서 수집 대상/주기/라벨 통제). 로그: 노드 Vector Agent가 경량 Push (로그는 이벤트 스트림이라 Pull 부적합)** |
| **D19** | **Service Discovery** *(v5)* | **Static vs File SD vs Consul vs DNS** | **File-based SD (Ansible 관리)** | **Airgap 환경에서 Consul/DNS SD 어려움. Ansible이 노드 추가/제거 시 JSON 파일 자동 생성 → vmagent이 60초마다 감지. K8s는 K8s SD로 자동 검색** |

---

## 13. 전체 SW Stack 요약 (v5)

```
┌──────────────────────────────────────────────────────────────────────┐
│                     GPU Monitoring Platform (v5)                         │
│                                                                       │
│  노드 배포 (Exporter + 경량 Agent, 모든 환경: Baremetal / K8s / VM)  │
│  ├── DCGM Exporter       - GPU 메트릭 HTTP 노출 (:9400, L1+L2)    │
│  │                          Pull 대기 (중앙 vmagent이 scrape)        │
│  │                          ⚠ L2 Profiling은 CUPTI 사용              │
│  ├── node_exporter       - 시스템 메트릭 HTTP 노출 (:9100)          │
│  │                          Pull 대기                                 │
│  ├── Vector Agent        - 경량 로그 forward (파싱 없음)             │
│  │                          → 중앙 Vector Aggregator에 Push          │
│  ├── 추론 서버 /metrics  - 추론 메트릭 HTTP 노출 (:8080)            │
│  │                          Pull 대기                                 │
│  └── ※ vmagent 없음!     - v5에서 노드에서 제거 (중앙으로 이동)     │
│                                                                       │
│  ★ 중앙 수집 레이어 (K8s 배포, v5 핵심)                              │
│  ├── vmagent (Central)   - 모든 노드 Exporter를 Pull scrape         │
│  │    HA 2+ replica        File SD (Ansible) + K8s SD                │
│  │                          → remote_write → VictoriaMetrics         │
│  └── Vector Aggregator   - 노드 Vector Agent에서 로그 수신           │
│                              파싱 / remap / 표준화                    │
│                              → ClickHouse sink                       │
│                                                                       │
│  K8s 전용 수집                                                        │
│  └── kube-state-metrics  - K8s 오브젝트 상태 (중앙 vmagent이 Pull)  │
│                                                                       │
│  레거시 메타데이터 수집 (v4~)                                         │
│  └── Metadata Collector  - S2 + VMware API 폴링 → ClickHouse         │
│      ├── S2 Adapters     - Jobs(시계열), Nodes(현재값),              │
│      │                     Projects/Pools(스냅샷, JSON)               │
│      └── VMware Adapter  - GPU VM Inventory (pyVmomi)                │
│                                                                       │
│  저장 (K8s 배포)                                                      │
│  ├── VictoriaMetrics Cluster - 시계열 메트릭 L1+L2                   │
│  └── ClickHouse Cluster      - 로그/프로파일링/메타데이터             │
│      (총 11개 테이블)                                                 │
│                                                                       │
│  서빙 (K8s 배포)                                                      │
│  ├── Grafana             - 통합 대시보드 (9개)                       │
│  ├── vmalert             - 알림 규칙 + L3 자동 트리거                │
│  ├── Alertmanager        - 알림 라우팅                                │
│  └── Custom Ingestion API - 수요/JSON 데이터 수집 (선택)             │
│                                                                       │
│  L2.5 Job 통계 (CUPTI 충돌 없음)                                     │
│  └── DCGM Job Stats     - S2 Job hook 연동, L2와 동시 가능          │
│                                                                       │
│  L3 온디맨드 프로파일링 (L2 일시 중단 필요)                           │
│  ├── Profiling Controller - L3 요청 관리 + L2 pause/resume           │
│  ├── Module A: PyTorch Profiler (학습)                               │
│  ├── Module B: Nsight Systems (범용, 비침습)                         │
│  └── Result Processor                                                │
│                                                                       │
│  기존 유지 (Baremetal 전용)                                           │
│  └── Zabbix              - IPMI/HW/SNMP 전용 (역할 축소)             │
│                                                                       │
│  버퍼링 (선택)                                                        │
│  └── Kafka (Strimzi)     - 대규모 시 메시지 버퍼                     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 14. 다음 단계

이 Plan이 확정되면:

1. **Phase 1 배포 코드** (v5 개정 필요):
   - 중앙 vmagent K8s Deployment + HA 매니페스트
   - File SD 타겟 파일 (Ansible 템플릿)
   - K8s SD scrape config
   - 방화벽 오픈 요청 (중앙 → 각 노드 :9400/:9100)
   - 노드 vmagent systemd 제거 Ansible playbook
2. **Phase 2 코드 작성**: Vector Aggregator K8s Deployment, Vector Agent 경량 config, ClickHouse DDL
3. **Phase 3 코드 작성** (v4~):
   - Metadata Collector 프로젝트 scaffolding (Python/Go)
   - S2 Adapter 구현 (S2 API 스펙 확인 필요)
   - VMware Adapter 구현 (vCenter 접속 정보 필요)
   - ClickHouse DDL (s2_jobs, s2_nodes, s2_projects, s2_pools, vmware_vm_inventory)
   - DCGM Job Stats 연동 (S2 Job hook)
   - K8s 매니페스트 (Deployment, ConfigMap, Secret)
   - Job Explorer / VM GPU Inventory Grafana 대시보드
4. **Phase 4 코드 작성**: vmalert 규칙, Alertmanager config
5. **Phase 5 코드 작성**: Profiling Controller (L2 pause/resume 포함)

### 추가로 확인이 필요한 사항

| 항목 | 내용 | 담당 |
|---|---|---|
| **방화벽 (v5)** | 중앙 vmagent K8s Pod → 각 GPU 노드 :9400/:9100 접근 허용 | 인프라/네트워크팀 |
| **S2 API 스펙** | REST API 엔드포인트, 인증 방식, 응답 형식 | S2 운영팀 확인 |
| **S2 CLI 사용 가능 여부** | API가 없을 경우 CLI 출력 형식 확인 | S2 운영팀 확인 |
| **vCenter 접속 정보** | vCenter URL, 서비스 계정, GPU VM 필터 조건 | VMware 인프라팀 확인 |
| **GPU Passthrough vs vGPU** | 현재 환경이 어느 방식인지 (수집 필드 차이) | VMware 인프라팀 확인 |
| **S2 Job↔GPU 인덱스 매핑** | S2가 어떤 형태로 GPU 인덱스를 제공하는지 | S2 운영팀 확인 |
| **네트워크 접근성** | K8s 클러스터 → S2 API, vCenter 간 방화벽 | 인프라/네트워크팀 확인 |

각 단계를 Claude Code로 함께 구현해 나갈 수 있습니다.