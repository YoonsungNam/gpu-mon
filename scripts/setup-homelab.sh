#!/bin/bash
# One-time homelab setup: installs helm, helmfile, and required plugins.

set -euo pipefail

HELM_VERSION="v3.16.4"
HELMFILE_VERSION="v0.169.2"

echo "=== gpu-mon homelab setup ==="

# ── helm ──────────────────────────────────────────────────────────────────────
if ! command -v helm &>/dev/null; then
    echo "[1/3] Installing helm ${HELM_VERSION}..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | \
        DESIRED_VERSION="${HELM_VERSION}" bash
else
    echo "[1/3] helm already installed: $(helm version --short)"
fi

# ── helmfile ──────────────────────────────────────────────────────────────────
if ! command -v helmfile &>/dev/null; then
    echo "[2/3] Installing helmfile ${HELMFILE_VERSION}..."
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
    VER="${HELMFILE_VERSION#v}"
    curl -fsSL "https://github.com/helmfile/helmfile/releases/download/${HELMFILE_VERSION}/helmfile_${VER}_${OS}_${ARCH}.tar.gz" | \
        tar xz helmfile
    sudo mv helmfile /usr/local/bin/
    helmfile version
else
    echo "[2/3] helmfile already installed: $(helmfile version)"
fi

# ── helm plugins ──────────────────────────────────────────────────────────────
echo "[3/3] Installing helm plugins..."
helm plugin list | grep -q diff || helm plugin install https://github.com/databus23/helm-diff
echo "helm-diff: OK"

echo ""
echo "=== Setup complete ==="
echo "Next: helmfile -e homelab sync"
