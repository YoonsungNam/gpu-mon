#!/usr/bin/env bash
# Sync all container images from public registries to corp internal registry.
# Reads versions.yaml as the single source of truth for image tags.
# See docs/corp-deployment-strategy.md (Phase 1) for design details.
#
# Usage: ./scripts/corp-sync-images.sh <corp-registry>
# Example: ./scripts/corp-sync-images.sh registry.corp.internal
# Requires: docker, yq
set -euo pipefail

DEST="${1:?Usage: ./scripts/corp-sync-images.sh <corp-registry>}"
SRC_REGISTRY="${REGISTRY:-ghcr.io/yoonsungnam/gpu-mon}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSIONS_FILE="${REPO_ROOT}/versions.yaml"

if [ ! -f "$VERSIONS_FILE" ]; then
  echo "ERROR: versions.yaml not found at $VERSIONS_FILE"
  exit 1
fi

for cmd in docker yq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: $cmd is required but not found in PATH"
    exit 1
  fi
done

echo "=== Syncing images to ${DEST} ==="
echo "Source registry: ${SRC_REGISTRY}"
echo "Versions file:  ${VERSIONS_FILE}"
echo ""

errors=0

# Sync OSS images (preserve original path: docker.io/victoriametrics/... → DEST/victoriametrics/...)
echo "--- OSS images ---"
while IFS=': ' read -r image tag; do
  [[ -z "$image" ]] && continue
  echo "  ${image}:${tag}"
  if ! docker pull "${image}:${tag}"; then
    echo "  ERROR: failed to pull ${image}:${tag}"
    errors=1
    continue
  fi
  docker tag "${image}:${tag}" "${DEST}/${image}:${tag}"
  if ! docker push "${DEST}/${image}:${tag}"; then
    echo "  ERROR: failed to push ${DEST}/${image}:${tag}"
    errors=1
  fi
done < <(yq '.oss_images | to_entries | .[] | .key + ": " + .value' "$VERSIONS_FILE")

# Sync custom images (preserve GHCR path: ghcr.io/yoonsungnam/gpu-mon/... → DEST/yoonsungnam/gpu-mon/...)
echo ""
echo "--- Custom images ---"
while IFS=': ' read -r image tag; do
  [[ -z "$image" ]] && continue
  src="${SRC_REGISTRY}/${image}:${tag}"
  # Strip the registry host, keep the org/repo path (yoonsungnam/gpu-mon/<image>)
  dst="${DEST}/${SRC_REGISTRY#*/}/${image}:${tag}"
  echo "  ${src} → ${dst}"
  if ! docker pull "${src}"; then
    echo "  ERROR: failed to pull ${src}"
    errors=1
    continue
  fi
  docker tag "${src}" "${dst}"
  if ! docker push "${dst}"; then
    echo "  ERROR: failed to push ${dst}"
    errors=1
  fi
done < <(yq '.custom_images | to_entries | .[] | .key + ": " + .value' "$VERSIONS_FILE")

echo ""
if [ "$errors" -ne 0 ]; then
  echo "=== Sync completed with errors (see above) ==="
  exit 1
fi
echo "=== Sync complete ==="
