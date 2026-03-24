SHELL := /bin/bash
.PHONY: help dev-up dev-down homelab-diff homelab-sync build-images build-grafana-plugins lint validate-values chart-diff corp-preflight corp-diff corp-sync corp-deploy corp-pull-charts corp-bundle

REGISTRY        ?= ghcr.io/yoonsungnam/gpu-mon
TAG             ?= dev
CORP_REGISTRY   ?= registry.corp.internal
CORP_CHARTS_DIR ?= /opt/gpu-mon/charts

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

corp-preflight: ## Validate corp symlinks and required files
	./scripts/validate-corp-setup.sh

corp-diff: corp-preflight ## Show pending Helm changes for corp env (requires gpu-mon-corp symlink)
	helmfile -e corp diff

corp-sync: corp-preflight ## Deploy to corp K8s cluster
	helmfile -e corp sync

corp-pull-charts: corp-preflight ## Pull OSS Helm charts to local .tgz cache (airgap prep)
	./scripts/corp-pull-charts.sh $(CORP_CHARTS_DIR)

corp-deploy: corp-preflight ## Sync images + deploy to corp cluster (one-touch)
	./scripts/corp-sync-images.sh $(CORP_REGISTRY)
	@# StorageClass is a cluster-scoped prerequisite, not managed by Helmfile.
	@# Only create if absent — avoids overwriting an SC managed externally.
	@if kubectl get sc spectrum-scale >/dev/null 2>&1; then \
		echo "StorageClass spectrum-scale already exists, skipping"; \
	elif [ -f environments/corp/storageclass.yaml ]; then \
		echo "Creating StorageClass spectrum-scale..."; \
		kubectl apply -f environments/corp/storageclass.yaml; \
	fi
	helmfile -e corp diff
	helmfile -e corp sync

corp-bundle: corp-preflight ## Generate Airgap bundle for corp deployment
	./scripts/airgap-bundle.sh

# ─── Images ──────────────────────────────────────────────────────────────────

build-images: ## Build all custom Docker images
	./scripts/build-images.sh $(REGISTRY) $(TAG)

push-images: ## Push images to registry
	./scripts/build-images.sh $(REGISTRY) $(TAG) --push

build-grafana-plugins: ## Build Grafana plugin carrier image (airgap)
	./scripts/build-grafana-plugins.sh $(REGISTRY) $(TAG)

push-grafana-plugins: ## Push Grafana plugin carrier image
	./scripts/build-grafana-plugins.sh $(REGISTRY) $(TAG) --push

# ─── Validate ────────────────────────────────────────────────────────────────

validate: ## Run deployment validation checks
	./scripts/validate-deployment.sh

lint: ## Lint Helm charts and Helmfile
	helm lint charts/vmagent-central
	helm lint charts/metadata-collector
	helm lint charts/mock-dcgm-exporter
	helmfile -e homelab lint

validate-values: ## Check override keys match chart defaults
	./scripts/validate-values-keys.sh homelab

chart-diff: ## Show values diff between chart versions (CHART=repo/name OLD=x NEW=y)
	@if [[ -z "$(CHART)" || -z "$(OLD)" || -z "$(NEW)" ]]; then \
		echo "Usage: make chart-diff CHART=repo/name OLD=x NEW=y" >&2; \
		exit 2; \
	fi
	@diff <(helm show values $(CHART) --version $(OLD)) \
	      <(helm show values $(CHART) --version $(NEW)) || true
