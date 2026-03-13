#!/bin/bash
# One-time macbook dev setup: checks Docker and starts the stack.

set -euo pipefail

echo "=== gpu-mon macbook setup ==="

# ── Docker ────────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop"
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "ERROR: Docker daemon is not running. Start Docker Desktop."
    exit 1
fi
echo "Docker: OK ($(docker version --format '{{.Server.Version}}'))"

# ── .env.local ────────────────────────────────────────────────────────────────
if [[ ! -f environments/macbook/.env.local ]]; then
    cp environments/macbook/.env.example environments/macbook/.env.local
    echo "Created environments/macbook/.env.local from example. Edit as needed."
fi

echo ""
echo "=== Setup complete ==="
echo "Run: make dev-up"
echo "Then: open http://localhost:3000 (Grafana, admin/admin)"
