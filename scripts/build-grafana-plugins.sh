#!/bin/bash
# Build the Grafana plugin carrier image for airgap environments.
# Plugin versions are read from versions.yaml (grafana_plugins section).
#
# Usage:
#   ./scripts/build-grafana-plugins.sh                              # local build only
#   ./scripts/build-grafana-plugins.sh ghcr.io/user/gpu-mon dev    # build + tag
#   ./scripts/build-grafana-plugins.sh ghcr.io/user/gpu-mon dev --push
#
# Requires: docker, yq

set -euo pipefail

REGISTRY="${1:-ghcr.io/yoonsungnam/gpu-mon}"
TAG="${2:-dev}"
PUSH="${3:-}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSIONS_FILE="${REPO_ROOT}/versions.yaml"

for cmd in docker yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: $cmd is required but not found in PATH"
    exit 1
  fi
done

CH_VER=$(yq '.grafana_plugins["grafana-clickhouse-datasource"]' "$VERSIONS_FILE")
VM_VER=$(yq '.grafana_plugins["victoriametrics-datasource"]' "$VERSIONS_FILE")

FULL_TAG="${REGISTRY}/grafana-plugins:${TAG}"

echo "→ Building ${FULL_TAG}"
echo "  grafana-clickhouse-datasource: ${CH_VER}"
echo "  victoriametrics-datasource:    ${VM_VER}"

docker build \
    --build-arg CLICKHOUSE_PLUGIN_VERSION="${CH_VER}" \
    --build-arg VM_DATASOURCE_VERSION="${VM_VER}" \
    -t "${FULL_TAG}" \
    "${REPO_ROOT}/src/grafana-plugins/"

if [[ "${PUSH}" == "--push" ]]; then
    echo "→ Pushing ${FULL_TAG}"
    docker push "${FULL_TAG}"
fi

echo "Done."
