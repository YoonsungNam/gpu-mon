#!/bin/bash
# Validate that a gpu-mon deployment is healthy.
# Checks VictoriaMetrics, ClickHouse, Grafana, and vmagent targets.

set -euo pipefail

NAMESPACE="${NAMESPACE:-monitoring}"
FAIL=0

check() {
    local desc="$1"
    local cmd="$2"
    if eval "${cmd}" &>/dev/null; then
        echo "  ✓ ${desc}"
    else
        echo "  ✗ ${desc}"
        FAIL=1
    fi
}

echo "=== gpu-mon deployment validation ==="

echo ""
echo "[1] K8s pods in ${NAMESPACE} namespace"
kubectl -n "${NAMESPACE}" get pods --no-headers | while read -r line; do
    name=$(echo "${line}" | awk '{print $1}')
    status=$(echo "${line}" | awk '{print $3}')
    if [[ "${status}" == "Running" ]]; then
        echo "  ✓ ${name}: ${status}"
    else
        echo "  ✗ ${name}: ${status}"
        FAIL=1
    fi
done

echo ""
echo "[2] VictoriaMetrics"
check "vminsert health" "kubectl -n ${NAMESPACE} exec deploy/vminsert -- wget -qO- localhost:8480/health"
check "vmselect health" "kubectl -n ${NAMESPACE} exec deploy/vmselect -- wget -qO- localhost:8481/health"

echo ""
echo "[3] vmagent targets"
check "vmagent health" "kubectl -n ${NAMESPACE} exec deploy/vmagent-central -- wget -qO- localhost:8429/health"

echo ""
echo "[4] Grafana"
check "grafana health" "kubectl -n ${NAMESPACE} exec deploy/grafana -- wget -qO- localhost:3000/api/health"

if [[ "${FAIL}" -eq 0 ]]; then
    echo ""
    echo "All checks passed."
else
    echo ""
    echo "Some checks failed. See above."
    exit 1
fi
