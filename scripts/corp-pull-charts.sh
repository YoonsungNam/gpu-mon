#!/usr/bin/env bash
# Pull all OSS Helm chart .tgz files needed by the corp environment.
# Reads versions.yaml as the single source of truth for chart versions.
#
# Usage: ./scripts/corp-pull-charts.sh <charts-dir>
# Example: ./scripts/corp-pull-charts.sh /opt/gpu-mon/charts
#
# Requires: helm, yq
set -euo pipefail

CHARTS_DIR="${1:?Usage: ./scripts/corp-pull-charts.sh <charts-dir>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSIONS_FILE="${REPO_ROOT}/versions.yaml"

if [ ! -f "$VERSIONS_FILE" ]; then
  echo "ERROR: versions.yaml not found at $VERSIONS_FILE"
  exit 1
fi

for cmd in helm yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: $cmd is required but not found in PATH"
    exit 1
  fi
done

mkdir -p "$CHARTS_DIR"

# Chart key → repo mapping (must match helmfile.yaml.gotmpl repositories)
declare -A CHART_REPOS=(
  ["victoria-metrics-cluster"]="victoriametrics/victoria-metrics-cluster"
  ["altinity-clickhouse-operator"]="clickhouse-operator/altinity-clickhouse-operator"
  ["grafana"]="grafana/grafana"
  ["vector"]="vector/vector"
)

declare -A REPO_URLS=(
  ["victoriametrics"]="https://victoriametrics.github.io/helm-charts"
  ["grafana"]="https://grafana.github.io/helm-charts"
  ["vector"]="https://helm.vector.dev"
  ["clickhouse-operator"]="https://docs.altinity.com/clickhouse-operator"
)

echo "=== Pulling Helm charts to ${CHARTS_DIR} ==="

# Add repos
for repo in "${!REPO_URLS[@]}"; do
  helm repo add "$repo" "${REPO_URLS[$repo]}" 2>/dev/null || true
done
helm repo update

# Pull each chart listed in versions.yaml
for key in "${!CHART_REPOS[@]}"; do
  chart="${CHART_REPOS[$key]}"
  version=$(yq ".helm_charts[\"$key\"]" "$VERSIONS_FILE")
  tgz="${CHARTS_DIR}/${key}-${version}.tgz"

  if [ -f "$tgz" ]; then
    echo "  Skip (exists): ${tgz}"
    continue
  fi

  echo "  Pull: ${chart} ${version}"
  helm pull "$chart" --version "$version" -d "$CHARTS_DIR"
done

echo "=== Charts ready ==="
ls -1 "$CHARTS_DIR"/*.tgz
