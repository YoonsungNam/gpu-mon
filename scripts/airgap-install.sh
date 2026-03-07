#!/bin/bash
# Airgap install script — runs on corp server after bundle transfer.
# Usage: ./install.sh <internal-registry-url>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="${1:?Usage: ./install.sh <registry.corp.internal>}"

echo "=== GPU Monitoring Airgap Install ==="
echo "Registry: ${REGISTRY}"

# ── 1. Load images + push to internal registry ────────────────────────────────
echo "[1/4] Loading images..."
docker load -i "${SCRIPT_DIR}/images/all-images.tar.gz"

echo "[1/4] Pushing to ${REGISTRY}..."
docker load -i "${SCRIPT_DIR}/images/all-images.tar.gz" 2>&1 | \
    grep "Loaded image:" | sed 's/Loaded image: //' | while read -r img; do
    new_tag="${REGISTRY}/${img##*/}"
    docker tag "${img}" "${new_tag}"
    docker push "${new_tag}"
done

# ── 2. Install tools ──────────────────────────────────────────────────────────
echo "[2/4] Installing tools..."

command -v helm &>/dev/null || { cp "${SCRIPT_DIR}/tools/helm" /usr/local/bin/; chmod +x /usr/local/bin/helm; }
command -v helmfile &>/dev/null || { cp "${SCRIPT_DIR}/tools/helmfile" /usr/local/bin/; chmod +x /usr/local/bin/helmfile; }

helm plugin list | grep -q diff || \
    cp -r "${SCRIPT_DIR}/tools/helm-plugins/helm-diff" "$(helm env HELM_PLUGINS)/"

# ── 3. Schema init ────────────────────────────────────────────────────────────
echo "[3/4] ClickHouse schemas will be initialized by the chart init job."

# ── 4. Deploy ─────────────────────────────────────────────────────────────────
echo "[4/4] Deploying with Helmfile..."
cd "${SCRIPT_DIR}/deploy"
export HELMFILE_CHARTS_DIR="${SCRIPT_DIR}/charts"
helmfile -e corp sync

echo ""
echo "=== Install complete ==="
echo "Check: kubectl -n monitoring get pods"
