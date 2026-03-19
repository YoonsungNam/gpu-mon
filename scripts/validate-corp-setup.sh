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

if [ "$errors" -ne 0 ]; then
  echo ""
  echo "Corp pre-flight check FAILED. Fix the issues above before deploying."
  exit 1
fi

echo "Corp pre-flight check passed."
