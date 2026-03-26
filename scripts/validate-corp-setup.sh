#!/usr/bin/env bash
# validate-corp-setup.sh — Pre-flight check for corp environment symlinks.
# Verifies that environments/corp/ exists and contains all required files
# before running helmfile -e corp commands.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CORP_DIR="${REPO_ROOT}/environments/corp"

REQUIRED_VALUES=(
  values.yaml
  vmagent.yaml
  victoriametrics.yaml
  clickhouse.yaml
  grafana.yaml
  vector.yaml
)

# metadata-collector is conditional on metadata_collector.enabled
OPTIONAL_VALUES=(
  metadata-collector.yaml
)

errors=0

# 1. Check environments/corp/ exists and resolves
if [ ! -d "$CORP_DIR" ]; then
  echo "ERROR: environments/corp/ does not exist."
  echo "  Run setup-symlinks.sh from the gpu-mon-corp repo first."
  exit 1
fi

# 2. Check each required values file
missing=()
for f in "${REQUIRED_VALUES[@]}"; do
  if [ ! -f "${CORP_DIR}/${f}" ]; then
    missing+=("$f")
    errors=1
  fi
done

if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: Missing required values files in environments/corp/:"
  for f in "${missing[@]}"; do
    echo "  - $f"
  done
fi

# 2b. Warn about missing optional values files
for f in "${OPTIONAL_VALUES[@]}"; do
  if [ ! -f "${CORP_DIR}/${f}" ]; then
    echo "WARN: Optional file missing: $f (needed if metadata_collector.enabled=true)"
  fi
done

# 3. Check that at least one target file exists
TARGETS_DIR="${CORP_DIR}/targets"
if [ ! -d "$TARGETS_DIR" ]; then
  echo "ERROR: environments/corp/targets/ directory does not exist."
  errors=1
elif [ -z "$(ls -A "$TARGETS_DIR" 2>/dev/null)" ]; then
  echo "ERROR: environments/corp/targets/ is empty — at least one target file is required."
  errors=1
fi

# 4. Print kubectl context/server info and warn for Rancher-style proxy endpoints.
if command -v kubectl >/dev/null 2>&1; then
  CURRENT_CONTEXT="$(kubectl config current-context 2>/dev/null || true)"
  if [ -n "$CURRENT_CONTEXT" ]; then
    CLUSTER_SERVER="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || true)"
    echo "INFO: kubectl current-context: $CURRENT_CONTEXT"
    if [ -n "$CLUSTER_SERVER" ]; then
      echo "INFO: kubectl cluster server: $CLUSTER_SERVER"
      if [[ "$CLUSTER_SERVER" == *"/k8s/clusters/"* ]] || [[ "$CLUSTER_SERVER" == *"rancher"* ]]; then
        echo "WARN: kubeconfig appears to target a Rancher proxy/API endpoint."
        echo "WARN: Client-side OpenAPI/schema fetches can time out through Rancher; corp StorageClass creation uses kubectl apply --validate=false for that reason."
      fi
    else
      echo "WARN: Unable to determine kubectl cluster server from the current kubeconfig."
    fi
  else
    echo "WARN: kubectl current-context is not set. Corp deploy will use whichever kubeconfig/context kubectl resolves at runtime."
  fi
else
  echo "WARN: kubectl is not installed or not in PATH. Corp deploy commands require kubectl."
fi

if [ "$errors" -ne 0 ]; then
  echo ""
  echo "Corp pre-flight check FAILED. Fix the issues above before deploying."
  exit 1
fi

echo "Corp pre-flight check passed."
