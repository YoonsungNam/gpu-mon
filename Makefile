.PHONY: help dev-up dev-down homelab-diff homelab-sync build-images lint

REGISTRY ?= ghcr.io/yoonsungnam/gpu-mon
TAG      ?= dev

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ─── macbook (Docker Compose) ────────────────────────────────────────────────

dev-up: ## Start full stack locally (macbook env, Docker Compose)
	./scripts/render-compose-clickhouse-schema.sh
	docker compose -f compose/docker-compose.yaml up -d

dev-down: ## Stop local stack
	docker compose -f compose/docker-compose.yaml down

dev-logs: ## Tail logs from local stack
	docker compose -f compose/docker-compose.yaml logs -f

dev-ps: ## Show running containers
	docker compose -f compose/docker-compose.yaml ps

# ─── homelab (K8s + Helmfile) ────────────────────────────────────────────────

homelab-diff: ## Show pending Helm changes for homelab env
	helmfile -e homelab diff

homelab-sync: ## Deploy to homelab K8s cluster
	helmfile -e homelab sync

homelab-destroy: ## Destroy homelab deployment (irreversible)
	helmfile -e homelab destroy

# ─── corp (requires private repo symlinked) ──────────────────────────────────

corp-diff: ## Show pending Helm changes for corp env (requires gpu-mon-corp symlink)
	helmfile -e corp diff

corp-sync: ## Deploy to corp K8s cluster
	helmfile -e corp sync

corp-bundle: ## Generate Airgap bundle for corp deployment
	./scripts/airgap-bundle.sh

# ─── Images ──────────────────────────────────────────────────────────────────

build-images: ## Build all custom Docker images
	./scripts/build-images.sh $(REGISTRY) $(TAG)

push-images: ## Push images to registry
	./scripts/build-images.sh $(REGISTRY) $(TAG) --push

# ─── Validate ────────────────────────────────────────────────────────────────

validate: ## Run deployment validation checks
	./scripts/validate-deployment.sh

lint: ## Lint Helm charts and Helmfile
	helm lint charts/vmagent-central
	helm lint charts/metadata-collector
	helm lint charts/mock-dcgm-exporter
	helmfile -e homelab lint
