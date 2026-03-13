# Corp Deployment Strategy

> Deployment strategy for airgapped production environment using Rancher (RKE2).
> Supersedes Scenario A/B in `deployment-workflow.md` and the former `corp-deployment.md` guide.
>
> **Looking for the quick version?** See [corp-quickstart.md](corp-quickstart.md).

## Prerequisites

- `gpu-mon-corp` private repo cloned alongside this repo (symlinked — see [corp-quickstart.md](corp-quickstart.md#one-time-symlink-setup))
- WSL with internet access (for image sync and Helm deploy)
- Docker CLI + `yq` installed on WSL, authenticated to both GHCR and corp registry

`environments/corp.example/` shows the expected structure without real values.
Copy to the private repo and fill in actual infrastructure details.

## Topology

```
                        Internet              Corp Network (Airgapped)
                        ────────              ────────────────────────

  GitHub (GHCR)  ◄──── WSL (Desktop) ────►  K8s API (RKE2 / Rancher)
  DockerHub             │                         │
  Helm repos            │                         ▼
                        └──────────────────► Corp Registry (Harbor)
                          docker/skopeo push       │
                          helm push (OCI)          ▼
                                              RKE2 Nodes (kubelet pull)
```

**Key constraint**: WSL has both internet access and corp network access.
K8s nodes are airgapped — they can only pull images from the corp registry.

This means every deploy has two phases:
1. **Sync** — mirror images + charts from internet to corp registry (via WSL)
2. **Deploy** — `helmfile sync` sends manifests to K8s API (via WSL)

---

## Evolution Path

The strategy progresses through three phases. Each phase builds on the previous
and can be adopted incrementally.

### Phase 1: Script-based Sync + Helmfile Deploy (start here)

**Prerequisite**: See [Prerequisites](#prerequisites) above.

```
 WSL
  ├─ ./scripts/corp-sync-images.sh   # pull from GHCR → tag → push to corp registry
  ├─ helmfile -e corp diff            # preview
  ├─ helmfile -e corp sync            # deploy (charts from local .tgz)
  └─ kubectl -n monitoring get pods   # verify
```

**How it works**:
- `corp-sync-images.sh` reads `versions.yaml` and pushes all images to corp registry
- Helmfile uses local `.tgz` charts (downloaded by the script or pre-packaged)
- Custom images are built by CI (GitHub Actions) and pulled from GHCR
- A single `make corp-deploy` command runs the full flow

**Pros**: Simple, no new infrastructure beyond Docker + registry access from WSL.
**Cons**: Full image pull+push every time (no layer dedup), manual version tracking.

**Artifacts**:
- `versions.yaml` — single source of truth for all image/chart versions
- `scripts/corp-sync-images.sh` — image sync script
- `Makefile` — `corp-deploy` target

---

### Phase 2: OCI Registry + Incremental Sync (medium-term)

**Prerequisite**: `skopeo` installed on WSL, Harbor or OCI-compatible registry on corp side.

```
 WSL
  ├─ skopeo sync (incremental — only changed layers transferred)
  ├─ helm push (charts as OCI artifacts to corp registry)
  ├─ helmfile -e corp sync (pulls charts from corp OCI registry)
  └─ done
```

**What changes from Phase 1**:
- Replace `docker pull/tag/push` with `skopeo copy` — transfers only missing layers
- Helm charts pushed as OCI artifacts to corp registry (not local `.tgz` files)
- Helmfile references `oci://registry.corp.internal/gpu-mon/charts/...` for corp env
- Version bump: edit `versions.yaml`, run `make corp-deploy` — everything else is automatic

**Pros**: Incremental sync (minutes instead of hours), charts versioned in registry.
**Cons**: Requires skopeo installation, OCI chart support in corp registry.

**Artifacts**:
- `scripts/corp-sync-registry.sh` — skopeo-based incremental sync
- Updated `helmfile.yaml.gotmpl` — OCI chart references for corp

---

### Phase 3: Rancher Catalog + UI Deploy (full maturity)

**Prerequisite**: Rancher v2.13+ with Apps & Marketplace enabled.

```
 WSL
  ├─ skopeo sync + helm push (same as Phase 2)
  └─ done (sync only)

 Rancher UI (operated by platform team)
  └─ Apps & Marketplace → gpu-mon → Upgrade → v1.2.0 → Install
```

**What changes from Phase 2**:
- An umbrella Helm chart (`charts/gpu-mon/`) wraps all sub-components
- Registered as a Rancher custom catalog (`ClusterRepo` CR)
- Operators deploy/upgrade/rollback from Rancher UI — no CLI needed
- WSL role reduces to "registry sync" only; deployment is self-service

**Pros**: UI-based deploy, RBAC-controlled, rollback from Rancher, multi-cluster ready.
**Cons**: Requires umbrella chart packaging, Rancher catalog configuration.

**Artifacts**:
- `charts/gpu-mon/Chart.yaml` — umbrella chart with sub-chart dependencies
- `rancher/cluster-repo.yaml` — Rancher catalog registration manifest

---

## Detailed Design: Phase 1 (Implementation Target)

### versions.yaml — Single Source of Truth

All image tags and chart versions in one file. Every script and template reads from here.

```yaml
# versions.yaml
custom_images:
  mock-dcgm-exporter: "v1.0.0"
  metadata-collector: "v1.0.0"

oss_images:
  victoriametrics/vminsert: "v1.106.1"
  victoriametrics/vmselect: "v1.106.1"
  victoriametrics/vmstorage: "v1.106.1"
  victoriametrics/vmagent: "v1.106.1"
  victoriametrics/vmalert: "v1.106.1"
  clickhouse/clickhouse-server: "24.8"
  altinity/clickhouse-operator: "0.24.0"
  grafana/grafana: "11.4.0"
  timberio/vector: "0.42.0-alpine"
  prom/alertmanager: "v0.27.0"
  prom/node-exporter: "v1.8.2"
  registry.k8s.io/kube-state-metrics/kube-state-metrics: "v2.14.0"

helm_charts:
  victoria-metrics-cluster: "0.36.0"
  grafana: "10.5.15"
  vector: "0.50.0"
  altinity-clickhouse-operator: "0.26.0"

tools:
  helm: "v3.16.4"
  helmfile: "v0.169.2"
```

### Image Path Strategy

Images must be pushed to the corp registry **preserving the original path structure**.
This is critical for RKE2 registry mirror compatibility (Phase 3), but we enforce it
from Phase 1 for consistency.

| Source | Corp Registry Path | Why |
|---|---|---|
| `victoriametrics/vmagent:v1.106.1` | `registry.corp.internal/victoriametrics/vmagent:v1.106.1` | Preserves upstream path |
| `ghcr.io/yoonsungnam/gpu-mon/mock-dcgm-exporter:v1.0.0` | `registry.corp.internal/yoonsungnam/gpu-mon/mock-dcgm-exporter:v1.0.0` | Preserves GHCR path |
| `registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.14.0` | `registry.corp.internal/registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.14.0` | Preserves full source path (sync script uses `versions.yaml` key as-is) |

With this convention:
- **Phase 1-2**: Corp `values.yaml` sets `image_registry: registry.corp.internal/yoonsungnam/gpu-mon`
  and charts resolve images correctly
- **Phase 3**: RKE2 registry mirrors redirect `ghcr.io` → `registry.corp.internal` transparently,
  so charts can use upstream image refs without rewriting

> **Note**: For non-DockerHub registries (e.g., `registry.k8s.io`), the Phase 1 sync script
> produces paths like `registry.corp.internal/registry.k8s.io/...` because it uses the
> `versions.yaml` key as-is. In Phase 3, RKE2 mirrors strip the source registry host,
> expecting `registry.corp.internal/kube-state-metrics/...` instead. The sync script must
> be updated to strip registry host prefixes before Phase 3 adoption.

### corp-sync-images.sh — Image Sync Script

Reads `versions.yaml`, pulls from public registries, pushes to corp registry
**preserving the original image path**.

```bash
#!/bin/bash
# Sync all container images to corp internal registry.
# Usage: ./scripts/corp-sync-images.sh <registry.corp.internal>
# Requires: docker, yq
set -euo pipefail

DEST="${1:?Usage: ./scripts/corp-sync-images.sh <registry.corp.internal>}"
SRC_REGISTRY="${REGISTRY:-ghcr.io/yoonsungnam/gpu-mon}"
VERSIONS_FILE="versions.yaml"

echo "=== Syncing images to ${DEST} ==="

# Sync OSS images (preserve original path: docker.io/victoriametrics/... → DEST/victoriametrics/...)
while IFS=': ' read -r image tag; do
    [[ -z "$image" ]] && continue
    echo "  ${image}:${tag}"
    docker pull "${image}:${tag}"
    docker tag  "${image}:${tag}" "${DEST}/${image}:${tag}"
    docker push "${DEST}/${image}:${tag}"
done < <(yq '.oss_images | to_entries | .[] | .key + ": " + .value' "${VERSIONS_FILE}")

# Sync custom images (preserve GHCR path: ghcr.io/yoonsungnam/gpu-mon/... → DEST/yoonsungnam/gpu-mon/...)
while IFS=': ' read -r image tag; do
    [[ -z "$image" ]] && continue
    src="${SRC_REGISTRY}/${image}:${tag}"
    # Strip the registry host, keep the full path (yoonsungnam/gpu-mon/<image>)
    dst="${DEST}/${SRC_REGISTRY#*/}/${image}:${tag}"
    echo "  ${src} → ${dst}"
    docker pull "${src}"
    docker tag  "${src}" "${dst}"
    docker push "${dst}"
done < <(yq '.custom_images | to_entries | .[] | .key + ": " + .value' "${VERSIONS_FILE}")

echo "=== Sync complete ==="
```

### Makefile Targets

```makefile
corp-deploy: ## Sync images + deploy to corp cluster (one-touch)
	./scripts/corp-sync-images.sh $(CORP_REGISTRY)
	helmfile -e corp diff
	helmfile -e corp sync

corp-sync: ## Sync images only (no deploy)
	./scripts/corp-sync-images.sh $(CORP_REGISTRY)
```

### Daily Workflow

```bash
# One-touch deploy (sync images + helmfile sync)
cd ~/work/gpu-mon
git pull && cd ../gpu-mon-corp && git pull && cd ../gpu-mon

make corp-deploy CORP_REGISTRY=registry.corp.internal

# Verify
kubectl -n monitoring get pods
```

---

## Detailed Design: Phase 2 (Incremental Sync)

### Replace docker pull/push with skopeo copy

```bash
# scripts/corp-sync-registry.sh
# Incremental — only transfers missing image layers

skopeo copy \
    "docker://victoriametrics/vmagent:v1.106.1" \
    "docker://${DEST}/victoriametrics/vmagent:v1.106.1" \
    --all  # multi-arch
```

**Why skopeo**: `docker pull` + `docker push` downloads the full image to local storage
then re-uploads. `skopeo copy` streams directly between registries and skips layers
that already exist at the destination. For a 3GB image set where only one image changed,
this reduces transfer from ~3GB to ~200MB.

### Helm charts as OCI artifacts

```bash
# Push charts to corp registry
helm push vmagent-central-0.1.0.tgz oci://registry.corp.internal/gpu-mon/charts
helm push clickhouse-cluster-0.1.0.tgz oci://registry.corp.internal/gpu-mon/charts
```

### Helmfile OCI chart references (corp environment)

```gotmpl
# helmfile.yaml.gotmpl — corp uses OCI chart refs
releases:
  - name: victoriametrics
    {{ if eq .Environment.Name "corp" }}
    chart: "oci://{{ .Values.image_registry }}/charts/victoria-metrics-cluster"
    version: "{{ .Values.helm_charts.victoria_metrics_cluster }}"
    {{ else }}
    chart: victoriametrics/victoria-metrics-cluster
    version: "0.36.0"
    {{ end }}
```

---

## Detailed Design: Phase 3 (Rancher Catalog)

Phase 3 integrates gpu-mon into Rancher's **Apps & Marketplace** so that operators
can install, upgrade, and rollback from the Rancher web UI without CLI access.

> **Reference**: [Using OCI-Based Helm Chart Repositories | Rancher](https://ranchermanager.docs.rancher.com/how-to-guides/new-user-guides/helm-charts-in-rancher/oci-repositories)
> **Reference**: [Creating Apps | Rancher](https://ranchermanager.docs.rancher.com/how-to-guides/new-user-guides/helm-charts-in-rancher/create-apps)
> **Reference**: [Helm Charts and Apps | Rancher](https://ranchermanager.docs.rancher.com/how-to-guides/new-user-guides/helm-charts-in-rancher)

### How Rancher Apps & Marketplace Works

Rancher's Apps feature is a Helm chart catalog with a web UI layer.
The mechanism is:

1. A `ClusterRepo` CR tells Rancher where to find Helm charts (Git, HTTP, or OCI registry)
2. Rancher periodically syncs the repo and lists available charts in the UI
3. Operators browse charts, select a version, fill in values via a form, and click Install
4. Behind the scenes, Rancher runs `helm install` / `helm upgrade` / `helm rollback`

OCI-based chart repositories are supported since **Rancher v2.9.0**.
Our target Rancher v2.13.1 fully supports this.

### Step 1: Umbrella Chart

Package all gpu-mon components as a single installable unit.

```yaml
# charts/gpu-mon/Chart.yaml
apiVersion: v2
name: gpu-mon
description: GPU Monitoring Platform — unified deployment
type: application
version: 1.0.0   # bumped with each release
appVersion: "1.0.0"
keywords:
  - monitoring
  - gpu
  - infrastructure
annotations:
  catalog.cattle.io/display-name: "GPU Monitoring"
  catalog.cattle.io/namespace: monitoring
  catalog.cattle.io/os: linux

dependencies:
  # Custom charts (bundled at package time via file://)
  - name: vmagent-central
    version: "0.1.x"
    repository: "file://../vmagent-central"
  - name: clickhouse-cluster
    version: "0.1.x"
    repository: "file://../clickhouse-cluster"
  - name: mock-dcgm-exporter
    version: "0.1.x"
    repository: "file://../mock-dcgm-exporter"
    condition: mock-dcgm-exporter.enabled
  - name: metadata-collector
    version: "0.1.x"
    repository: "file://../metadata-collector"
    condition: metadata-collector.enabled

  # OSS charts (pulled from upstream at package time)
  - name: victoria-metrics-cluster
    version: "0.36.0"
    repository: "https://victoriametrics.github.io/helm-charts"
  - name: grafana
    version: "10.5.15"
    repository: "https://grafana.github.io/helm-charts"
  - name: vector
    version: "0.50.0"
    repository: "https://helm.vector.dev"
  - name: altinity-clickhouse-operator
    version: "0.26.0"
    repository: "https://docs.altinity.com/clickhouse-operator"
```

**Rancher-specific annotations** (in `Chart.yaml`):

| Annotation | Purpose |
|---|---|
| `catalog.cattle.io/display-name` | Shown in UI instead of chart name |
| `catalog.cattle.io/namespace` | Fixed target namespace for deployment |
| `catalog.cattle.io/release-name` | Fixed Helm release name |
| `catalog.cattle.io/auto-install` | Pre-install dependency charts (e.g., CRDs) |
| `catalog.cattle.io/requests-cpu` | Resource requirement warning in UI |
| `catalog.cattle.io/os` | Restrict to `linux` or `windows` nodes |

### Step 2: questions.yaml (User-Friendly Install Form)

Rancher renders `questions.yaml` as a structured form in the UI,
replacing raw YAML editing with labeled fields, dropdowns, and checkboxes.

```yaml
# charts/gpu-mon/questions.yaml
questions:
  # ── Registry ────────────────────────────────────────────────
  - variable: global.image_registry
    label: "Internal Container Registry"
    description: "Corp registry URL where all images are mirrored"
    type: string
    required: true
    default: "registry.corp.internal"
    group: "Registry"

  - variable: global.image_tag
    label: "Image Tag"
    description: "Tag for custom gpu-mon images (e.g., v1.2.0)"
    type: string
    required: true
    default: "v1.0.0"
    group: "Registry"

  # ── Features ────────────────────────────────────────────────
  - variable: metadata-collector.enabled
    label: "Enable Metadata Collector"
    description: "Collect batch scheduler (S2) and VMware vCenter metadata"
    type: boolean
    default: true
    group: "Features"

  - variable: mock-dcgm-exporter.enabled
    label: "Enable Mock DCGM Exporter"
    description: "Synthetic GPU metrics for testing (disable in production)"
    type: boolean
    default: false
    group: "Features"

  # ── Storage ─────────────────────────────────────────────────
  - variable: global.retention.metrics_days
    label: "Metrics Retention (days)"
    description: "VictoriaMetrics data retention period"
    type: int
    default: 90
    group: "Storage"

  - variable: global.retention.logs_days
    label: "Logs Retention (days)"
    description: "ClickHouse gpu_unified_logs TTL"
    type: int
    default: 30
    group: "Storage"

  # ── Grafana ─────────────────────────────────────────────────
  - variable: grafana.ingress.hosts[0]
    label: "Grafana Hostname"
    description: "Ingress hostname for Grafana dashboard"
    type: string
    default: "grafana.monitoring.corp.internal"
    group: "Access"
```

**How it looks in Rancher UI**:

```
┌──────────────────────────────────────────────────────────┐
│  Install: GPU Monitoring  v1.2.0                         │
│                                                          │
│  ── Registry ──────────────────────────────────────────  │
│  Internal Container Registry: [registry.corp.internal ]  │
│  Image Tag:                   [v1.2.0                 ]  │
│                                                          │
│  ── Features ──────────────────────────────────────────  │
│  Enable Metadata Collector:   [x]                        │
│  Enable Mock DCGM Exporter:   [ ]                        │
│                                                          │
│  ── Storage ───────────────────────────────────────────  │
│  Metrics Retention (days):    [90 ]                      │
│  Logs Retention (days):       [30 ]                      │
│                                                          │
│  ── Access ────────────────────────────────────────────  │
│  Grafana Hostname: [grafana.monitoring.corp.internal  ]  │
│                                                          │
│                    [Cancel]  [Install]                    │
└──────────────────────────────────────────────────────────┘
```

### Step 3: Package and Push Chart to Corp Registry (from WSL)

```bash
# Build umbrella chart (resolves all file:// and remote dependencies)
cd ~/work/gpu-mon
helm dependency build charts/gpu-mon/

# Package into .tgz
helm package charts/gpu-mon/
# → gpu-mon-1.2.0.tgz

# Push as OCI artifact to corp registry
helm push gpu-mon-1.2.0.tgz oci://registry.corp.internal/gpu-mon/charts
```

After this, the chart exists at:
`oci://registry.corp.internal/gpu-mon/charts/gpu-mon:1.2.0`

### Step 4: Register OCI Repository in Rancher (one-time)

**Via Rancher UI**:

```
Rancher Dashboard
  → Cluster Management
    → [corp-cluster] → Explore
      → Apps → Repositories → Create
        Name:   gpu-mon
        Target: OCI Repository
        OCI URL: registry.corp.internal/gpu-mon/charts
        Auth:   (select BasicAuth secret if registry requires credentials)
```

**Via kubectl** (equivalent manifest):

```yaml
# rancher/cluster-repo.yaml
apiVersion: catalog.cattle.io/v1
kind: ClusterRepo
metadata:
  name: gpu-mon
spec:
  url: oci://registry.corp.internal/gpu-mon/charts
  # Optional: authentication for private registry
  clientSecret:
    name: gpu-mon-registry-auth      # kubernetes.io/basic-auth Secret
    namespace: cattle-system
  # Optional: custom refresh interval (default: 3600s)
  # refreshInterval: 1800
```

```yaml
# rancher/registry-auth-secret.yaml (if needed)
apiVersion: v1
kind: Secret
metadata:
  name: gpu-mon-registry-auth
  namespace: cattle-system
type: kubernetes.io/basic-auth
stringData:
  username: "gpu-mon-read"
  password: "HARBOR_TOKEN_HERE"        # store in gpu-mon-corp, not here
```

Rancher syncs the repository automatically (default: every 6 hours).
Manual refresh: Apps → Repositories → gpu-mon → Refresh.

### Step 5: Install via Rancher UI

Once the `ClusterRepo` status is **Active**, gpu-mon appears in the chart catalog:

```
Rancher Dashboard
  → [corp-cluster] → Explore
    → Apps → Charts → (filter: "gpu-mon" repository)

┌──────────────────────────────────────────────────┐
│  Charts                          [gpu-mon ▼]     │
│                                                  │
│  ┌─────────────┐                                 │
│  │ GPU          │  GPU Monitoring Platform        │
│  │ Monitoring   │  v1.2.0                         │
│  │  [Install]   │                                 │
│  └─────────────┘                                 │
└──────────────────────────────────────────────────┘
```

Click **Install** → fill in values form (from questions.yaml) → **Install**.
Rancher creates a Helm job that runs `helm install`.
Console output is visible in **Apps → Recent Operations**.

### Step 6: Upgrade via Rancher UI

When a new chart version is pushed to the registry (e.g., v1.3.0),
Rancher detects it on the next sync and shows an upgrade indicator:

```
Rancher Dashboard → Apps → Installed Apps

┌────────────────────────────────────────────────────────┐
│  Installed Apps                                        │
│                                                        │
│  Name        Namespace    Version    Upgradable        │
│  ──────────  ──────────   ───────    ──────────────    │
│  gpu-mon     monitoring   1.2.0      1.3.0 available   │
│                                      [Upgrade]         │
└────────────────────────────────────────────────────────┘
```

Click **Upgrade** →

```
┌──────────────────────────────────────────────────────────┐
│  Upgrade: GPU Monitoring                                 │
│                                                          │
│  Current: 1.2.0  →  Target: [1.3.0          ▼]          │
│                                                          │
│  ── Values (pre-filled from current install) ──────────  │
│  Internal Container Registry: [registry.corp.internal ]  │
│  Image Tag:                   [v1.3.0                 ]  │
│  Enable Metadata Collector:   [x]                        │
│  ...                                                     │
│                                                          │
│  Previous values are preserved. Only change what's new.  │
│                                                          │
│                    [Cancel]  [Upgrade]                    │
└──────────────────────────────────────────────────────────┘
```

Rancher runs `helm upgrade` with the merged values.

### Step 7: Rollback via Rancher UI

```
Apps → Installed Apps → gpu-mon → [...] menu → Rollback
  → Select revision: [Revision 1 — v1.2.0]
  → [Rollback]
```

Rancher runs `helm rollback` to restore the previous release.

### Upgrade Lifecycle (Phase 3 End-to-End)

```
 Developer (WSL)                         Rancher UI (Operator)
 ───────────────                         ─────────────────────

 1. Update versions.yaml (v1.3.0)
 2. Update charts/gpu-mon/Chart.yaml
    version: 1.3.0

 3. Sync images to corp registry:
    make corp-sync CORP_REGISTRY=...

 4. Package + push chart:
    helm dependency build charts/gpu-mon/
    helm package charts/gpu-mon/
    helm push gpu-mon-1.3.0.tgz \        5. Rancher auto-detects v1.3.0
      oci://registry.corp.internal/...       (or click Refresh on repo)

                                          6. "Upgradable: 1.3.0" appears
                                          7. Click Upgrade → review values
                                          8. Click Upgrade → Helm job runs
                                          9. Pods rolling update
                                         10. If issues → Rollback to v1.2.0
```

### RKE2 Registry Mirror (one-time node config)

```yaml
# /etc/rancher/rke2/registries.yaml (applied by Ansible to all nodes)
mirrors:
  "ghcr.io":
    endpoint:
      - "https://registry.corp.internal"
  "docker.io":
    endpoint:
      - "https://registry.corp.internal"
  "registry.k8s.io":
    endpoint:
      - "https://registry.corp.internal"
configs:
  "registry.corp.internal":
    tls:
      ca_file: /etc/pki/ca-trust/source/anchors/corp-ca.crt
```

With registry mirrors configured, RKE2 transparently rewrites the registry host
while **preserving the image path**. For example:

```
Chart references:           ghcr.io/yoonsungnam/gpu-mon/mock-dcgm-exporter:v1.0.0
Mirror rewrites to:         registry.corp.internal/yoonsungnam/gpu-mon/mock-dcgm-exporter:v1.0.0
```

This is why the sync script preserves the original path structure (see "Image Path
Strategy" in Phase 1) — the mirrored path must match exactly what kubelet requests.
No image reference rewriting is needed in chart values.

### Constraints and Limitations

| Item | Detail |
|---|---|
| OCI chart size limit | Rancher supports charts up to **20 MB** (gpu-mon umbrella chart is well under this) |
| Auto-refresh interval | Default **6 hours** (configurable via `spec.refreshInterval` on ClusterRepo) |
| Auth method | **BasicAuth** only for OCI repos (create `kubernetes.io/basic-auth` Secret) |
| Rancher version | OCI repos require **Rancher v2.9.0+** (our v2.13.1 is supported) |
| Upgrade display | Known issue: upgradeable version info may not display immediately after push — use manual Refresh |

---

## Version Lifecycle

### Phase 1 & 2 (CLI-driven)

```
  Developer                CI                      WSL                  Corp K8s
  ─────────                ──                      ───                  ────────
  bump versions.yaml
  merge to main
  git tag v1.2.0 ────► Build custom images
                        Push to GHCR
                        Package charts ──────► git pull
                                               make corp-deploy
                                               ├─ sync images ──────► registry
                                               └─ helmfile sync ────► K8s API
                                                                        │
                                                                        ▼
                                                                     Pods updated
```

### Phase 3 (Rancher UI-driven)

```
  Developer                CI              WSL                 Rancher UI        Corp K8s
  ─────────                ──              ───                 ──────────        ────────
  bump versions.yaml
  merge to main
  git tag v1.2.0 ────► Build images
                        Push to GHCR ──► git pull
                                         make corp-sync ───► registry
                                         helm push chart ──► registry
                                                               │
                                                         (auto-refresh)
                                                               │
                                                       Operator sees ──────► helm upgrade
                                                       "Upgradable 1.2.0"       │
                                                       clicks [Upgrade]          ▼
                                                                            Pods updated
```

### Release Checklist

**Phase 1 & 2:**

1. Update `versions.yaml` (bump custom image versions, update OSS if needed)
2. Merge to `main`, tag `vX.Y.Z`
3. CI builds and pushes custom images to GHCR
4. On WSL: `git pull && make corp-deploy CORP_REGISTRY=registry.corp.internal`
5. Verify: `kubectl -n monitoring get pods`

**Phase 3 (additional steps):**

1. Update `versions.yaml` + `charts/gpu-mon/Chart.yaml` version
2. Merge to `main`, tag `vX.Y.Z`
3. CI builds and pushes custom images to GHCR
4. On WSL:
   ```bash
   git pull
   make corp-sync CORP_REGISTRY=registry.corp.internal
   helm dependency build charts/gpu-mon/
   helm package charts/gpu-mon/
   helm push gpu-mon-X.Y.Z.tgz oci://registry.corp.internal/gpu-mon/charts
   ```
5. Operator: Rancher UI → Apps → Installed Apps → gpu-mon → Upgrade → vX.Y.Z
6. Verify: Rancher UI → Workloads → Pods (or `kubectl -n monitoring get pods`)

---

## Rollback

### CLI Rollback (Phase 1 & 2)

```bash
# Single release rollback
helm -n monitoring rollback victoriametrics 1

# Full rollback to previous version
git checkout v1.1.0
make corp-deploy CORP_REGISTRY=registry.corp.internal
```

### Rancher UI Rollback (Phase 3)

```
Rancher Dashboard → Apps → Installed Apps → gpu-mon → [...] → Rollback
  → Select: Revision 1 (v1.1.0)
  → Click [Rollback]
```

Rancher runs `helm rollback` under the hood. Previous images remain in the
corp registry, so no re-sync is needed for rollback.

---

## Comparison with Previous Strategy

| Aspect | Previous (Scenario A/B) | Refined (Phase 1→3) |
|--------|------------------------|----------------------|
| Version tracking | Manual, scattered across files | `versions.yaml` single source |
| Image sync | Manual `docker tag/push` per image | Scripted, reads versions.yaml |
| Daily deploy command | 5 manual steps | `make corp-deploy` |
| Incremental updates | Full bundle every time (3-5GB) | Phase 2: layer-level dedup |
| Rancher integration | None | Phase 3: UI catalog |
| Rollback | Re-transfer old bundle | `helm rollback` or git checkout + redeploy |
| Airgap bundle | Still available as fallback | Keep `scripts/airgap-bundle.sh` for disaster recovery |

---

## Decision Log

| # | Decision | Rationale |
|---|----------|-----------|
| D20 | Phase 1 uses docker pull/push, not skopeo | Minimize WSL tool dependencies at start; skopeo added in Phase 2 |
| D21 | Keep airgap-bundle.sh as fallback | Disaster recovery when WSL or registry access is lost |
| D22 | Umbrella chart deferred to Phase 3 | Helmfile is sufficient until Rancher UI integration is needed |
| D23 | RKE2 registry mirrors over image rewriting | Transparent redirect avoids chart modifications; standard RKE2 pattern |
| D24 | versions.yaml as SSOT over Chart.yaml appVersion | Single file drives CI, sync scripts, Helmfile, and compose — avoids multi-file drift |
| D25 | Rancher OCI catalog over Git-based catalog | OCI repos store chart + images in same registry; Git-based repos need separate image delivery |
| D26 | questions.yaml for Rancher UI form | Structured install form prevents misconfiguration by operators unfamiliar with Helm values |

---

## References

- [Helm Charts and Apps | Rancher](https://ranchermanager.docs.rancher.com/how-to-guides/new-user-guides/helm-charts-in-rancher) — Overview of Rancher Apps & Marketplace
- [Using OCI-Based Helm Chart Repositories | Rancher](https://ranchermanager.docs.rancher.com/how-to-guides/new-user-guides/helm-charts-in-rancher/oci-repositories) — ClusterRepo setup for OCI registries
- [Creating Apps | Rancher](https://ranchermanager.docs.rancher.com/how-to-guides/new-user-guides/helm-charts-in-rancher/create-apps) — Chart structure, annotations, questions.yaml
- [Helm Charts in Rancher (subheaders) | Rancher](https://ranchermanager.docs.rancher.com/pages-for-subheaders/helm-charts-in-rancher) — Navigation page for all Helm-related docs
- [Air-Gapped Install: Publish Images | Rancher](https://ranchermanager.docs.rancher.com/v2.8/getting-started/installation-and-upgrade/other-installation-methods/air-gapped-helm-cli-install/publish-images) — Official airgap image mirroring guide
