#!/usr/bin/env bash
# helmfile-sync.sh — Helmfile sync wrapper with StatefulSet immutable field handling
#
# Kubernetes StatefulSets have immutable fields (volumeClaimTemplates: size,
# storageClassName, accessModes). When Helm tries to update these, the API
# server rejects the change and helmfile sync fails.
#
# This wrapper detects such failures, orphan-deletes the affected StatefulSets
# (pods and PVCs keep running), and retries. The recreated StatefulSet adopts
# the existing pods.
#
# Usage: ./scripts/helmfile-sync.sh <environment>
#
# Note: ClickHouse StatefulSets are operator-managed and excluded here.
# Immutable field changes on ClickHouse require ClickHouseInstallation CR
# recreation through the operator.

set -euo pipefail

ENV="${1:?Usage: $0 <environment>}"

# Helm-managed StatefulSets that may hit immutable field errors.
# Format: "namespace/statefulset-name"
MANAGED_STATEFULSETS=(
  "monitoring/victoriametrics-victoria-metrics-cluster-vmstorage"
)

find_affected_statefulsets() {
  local output="$1"
  local matched=()
  local entry
  local sts

  for entry in "${MANAGED_STATEFULSETS[@]}"; do
    sts="${entry#*/}"
    # K8s API errors quote the resource name: StatefulSet.apps "name" is invalid
    if [[ "$output" == *"\"$sts\""* ]]; then
      matched+=("$entry")
    fi
  done

  if [ ${#matched[@]} -gt 0 ]; then
    printf '%s\n' "${matched[@]}"
  fi
}

echo "=== gpu-mon helmfile sync ($ENV) ==="

# Attempt helmfile sync, capturing output and exit code separately.
set +e
OUTPUT=$(helmfile -e "$ENV" sync 2>&1)
RC=$?
set -e

# Success — print output and exit.
if [ $RC -eq 0 ]; then
  echo "$OUTPUT"
  echo "=== sync completed successfully ==="
  exit 0
fi

# Check if the failure is due to immutable field errors.
if ! echo "$OUTPUT" | grep -qi "field is immutable"; then
  # Not an immutable field issue — surface the original error.
  echo "$OUTPUT"
  exit "$RC"
fi

echo "$OUTPUT"
echo ""
echo "[retry] Detected immutable field error in StatefulSet update."

AFFECTED_STATEFULSETS=()
while IFS= read -r entry; do
  if [ -n "$entry" ]; then
    AFFECTED_STATEFULSETS+=("$entry")
  fi
done < <(find_affected_statefulsets "$OUTPUT")

if [ ${#AFFECTED_STATEFULSETS[@]} -eq 0 ]; then
  echo "[retry] No allowlisted StatefulSet was named in the error output."
  echo "[retry] Refusing to orphan-delete healthy resources; inspect the sync failure above."
  exit "$RC"
fi

echo "[retry] Orphan-deleting only the affected StatefulSets named in the error output..."

for entry in "${AFFECTED_STATEFULSETS[@]}"; do
  ns="${entry%%/*}"
  sts="${entry#*/}"
  echo "[retry]   Orphan-deleting $ns/$sts..."
  DELETE_ERR=$(kubectl delete statefulset "$sts" -n "$ns" --cascade=orphan 2>&1) && {
    echo "[retry]   $ns/$sts deleted — pods still running."
  } || {
    if echo "$DELETE_ERR" | grep -qi "not found"; then
      echo "[retry]   $ns/$sts not found — already deleted, skipping."
    else
      echo "[retry]   Failed to delete $ns/$sts:" >&2
      echo "$DELETE_ERR" >&2
      exit 1
    fi
  }
done

echo "[retry] Re-running helmfile sync..."
if ! helmfile -e "$ENV" sync; then
  echo "[retry] Retry also failed. Manual intervention required."
  exit 1
fi
echo "=== sync completed after StatefulSet recreation ==="
