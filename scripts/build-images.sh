#!/bin/bash
# Build all custom Docker images for gpu-mon.
#
# Usage:
#   ./scripts/build-images.sh                              # local build only
#   ./scripts/build-images.sh ghcr.io/user/gpu-mon dev    # build + tag
#   ./scripts/build-images.sh ghcr.io/user/gpu-mon dev --push

set -euo pipefail

REGISTRY="${1:-ghcr.io/yoonsungnam/gpu-mon}"
TAG="${2:-dev}"
PUSH="${3:-}"

IMAGES=(
    "mock-dcgm-exporter"
    "metadata-collector"
)

for img in "${IMAGES[@]}"; do
    src="src/${img}"
    full_tag="${REGISTRY}/${img}:${TAG}"

    echo "→ Building ${full_tag} from ${src}/"
    docker build -t "${full_tag}" "${src}/"

    if [[ "${PUSH}" == "--push" ]]; then
        echo "→ Pushing ${full_tag}"
        docker push "${full_tag}"
    fi
done

echo "Done."
