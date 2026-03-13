#!/bin/bash
# Generate an Airgap deployment bundle for corp environment.
# See docs/corp-deployment-strategy.md for the full deployment guide.
#
# Requires: docker, helm, helmfile, curl
# Assumes environments/corp/ is symlinked from gpu-mon-corp repo.

set -euo pipefail

BUNDLE_DIR="./airgap-bundle"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BUNDLE_NAME="gpu-mon-airgap-${TIMESTAMP}"
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-ghcr.io/yoonsungnam/gpu-mon}"

echo "=== GPU Monitoring Airgap Bundle ==="
echo "Tag: ${TAG}, Registry: ${REGISTRY}"

rm -rf "${BUNDLE_DIR}"
mkdir -p "${BUNDLE_DIR}"/{images,charts,deploy,tools}

# ── 1. Container images ───────────────────────────────────────────────────────
echo "[1/5] Pulling and saving container images..."

OSS_IMAGES=(
    "victoriametrics/vminsert:v1.106.1"
    "victoriametrics/vmselect:v1.106.1"
    "victoriametrics/vmstorage:v1.106.1"
    "victoriametrics/vmagent:v1.106.1"
    "victoriametrics/vmalert:v1.106.1"
    "clickhouse/clickhouse-server:24.8"
    "altinity/clickhouse-operator:0.24.0"
    "grafana/grafana:11.4.0"
    "timberio/vector:0.42.0-alpine"
    "prom/alertmanager:v0.27.0"
    "prom/node-exporter:v1.8.2"
    "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.14.0"
)
CUSTOM_IMAGES=(
    "${REGISTRY}/mock-dcgm-exporter:${TAG}"
    "${REGISTRY}/metadata-collector:${TAG}"
)

ALL_IMAGES=("${OSS_IMAGES[@]}" "${CUSTOM_IMAGES[@]}")

for img in "${ALL_IMAGES[@]}"; do
    echo "  Pull: ${img}"
    docker pull "${img}" 2>/dev/null || echo "  WARN: skip (not found): ${img}"
done

docker save "${ALL_IMAGES[@]}" | gzip > "${BUNDLE_DIR}/images/all-images.tar.gz"
echo "  Saved: $(du -sh "${BUNDLE_DIR}/images/all-images.tar.gz" | cut -f1)"

# ── 2. Helm charts ────────────────────────────────────────────────────────────
echo "[2/5] Downloading Helm charts..."

helm repo add victoriametrics https://victoriametrics.github.io/helm-charts 2>/dev/null || true
helm repo add grafana https://grafana.github.io/helm-charts 2>/dev/null || true
helm repo add vector https://helm.vector.dev 2>/dev/null || true
helm repo add clickhouse-operator https://docs.altinity.com/clickhouse-operator 2>/dev/null || true
helm repo update

helm pull victoriametrics/victoria-metrics-cluster --version 0.36.0 -d "${BUNDLE_DIR}/charts/"
helm pull grafana/grafana --version 10.5.15 -d "${BUNDLE_DIR}/charts/"
helm pull vector/vector --version 0.50.0 -d "${BUNDLE_DIR}/charts/"
helm pull clickhouse-operator/altinity-clickhouse-operator --version 0.26.0 -d "${BUNDLE_DIR}/charts/"

for chart_dir in charts/*/; do
    [[ -f "${chart_dir}/Chart.yaml" ]] && helm package "${chart_dir}" -d "${BUNDLE_DIR}/charts/"
done

# ── 3. Deploy files ───────────────────────────────────────────────────────────
echo "[3/5] Copying deploy files..."

cp helmfile.yaml.gotmpl "${BUNDLE_DIR}/deploy/"
cp environments/defaults.yaml "${BUNDLE_DIR}/deploy/"
mkdir -p "${BUNDLE_DIR}/deploy/environments"
cp -rL environments/corp "${BUNDLE_DIR}/deploy/environments/corp"
cp -r schemas/ "${BUNDLE_DIR}/deploy/schemas/"
cp -r dashboards/ "${BUNDLE_DIR}/deploy/dashboards/"
cp -r alerting/ "${BUNDLE_DIR}/deploy/alerting/"

# ── 4. Tool binaries ──────────────────────────────────────────────────────────
echo "[4/5] Downloading tool binaries (linux/amd64)..."

HELM_VER="v3.16.4"
HELMFILE_VER="v0.169.2"

curl -fsSL "https://get.helm.sh/helm-${HELM_VER}-linux-amd64.tar.gz" | \
    tar xz -C "${BUNDLE_DIR}/tools/" linux-amd64/helm --strip-components=1

curl -fsSL "https://github.com/helmfile/helmfile/releases/download/${HELMFILE_VER}/helmfile_${HELMFILE_VER#v}_linux_amd64.tar.gz" | \
    tar xz -C "${BUNDLE_DIR}/tools/" helmfile

mkdir -p "${BUNDLE_DIR}/tools/helm-plugins/"
helm plugin list | grep -q diff || helm plugin install https://github.com/databus23/helm-diff
cp -r "$(helm env HELM_PLUGINS)/helm-diff" "${BUNDLE_DIR}/tools/helm-plugins/"

# ── 5. Install script ─────────────────────────────────────────────────────────
echo "[5/5] Generating install.sh..."
cp scripts/airgap-install.sh "${BUNDLE_DIR}/install.sh"
chmod +x "${BUNDLE_DIR}/install.sh"

# ── Final bundle ──────────────────────────────────────────────────────────────
echo ""
echo "Compressing bundle..."
tar czf "${BUNDLE_NAME}.tar.gz" -C "${BUNDLE_DIR}" .
echo ""
echo "✓ Bundle: ${BUNDLE_NAME}.tar.gz  ($(du -sh "${BUNDLE_NAME}.tar.gz" | cut -f1))"
echo ""
echo "Transfer to corp server, then:"
echo "  tar xzf ${BUNDLE_NAME}.tar.gz"
echo "  ./install.sh <registry.corp.internal>"
