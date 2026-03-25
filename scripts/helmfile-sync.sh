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
echo "[retry] Orphan-deleting affected StatefulSets (pods and PVCs preserved)..."

for entry in "${MANAGED_STATEFULSETS[@]}"; do
  ns="${entry%%/*}"
  sts="${entry#*/}"
  if kubectl get statefulset "$sts" -n "$ns" &>/dev/null; then
    echo "[retry]   Orphan-deleting $ns/$sts..."
    kubectl delete statefulset "$sts" -n "$ns" --cascade=orphan
    echo "[retry]   $ns/$sts deleted — pods still running."
  fi
done

echo "[retry] Re-running helmfile sync..."
helmfile -e "$ENV" sync
echo "=== sync completed after StatefulSet recreation ==="
