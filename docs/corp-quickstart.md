# Corp Deployment Quickstart (Phase 1)

> Minimal steps to deploy gpu-mon to the airgapped corp cluster.
> For full design and Phase 2/3 evolution, see [corp-deployment-strategy.md](corp-deployment-strategy.md).

## Prerequisites

- Docker CLI + `yq` installed on WSL
- Authenticated to both GHCR and corp registry (`docker login`)
- `gpu-mon-corp` private repo cloned alongside this repo

### One-time symlink setup

```bash
cd ~/work/gpu-mon/environments/
ln -s ../../gpu-mon-corp/environments/corp ./corp

cd ~/work/gpu-mon/ansible/inventory/
ln -s ../../../gpu-mon-corp/ansible/inventory/corp.ini ./corp.ini

cd ~/work/gpu-mon/alerting/alertmanager/
ln -s ../../../gpu-mon-corp/alerting/alertmanager/corp.yaml ./corp.yaml
```

## Deploy

```bash
cd ~/work/gpu-mon
git pull && cd ../gpu-mon-corp && git pull && cd ../gpu-mon

# Sync images + deploy (one command)
make corp-deploy CORP_REGISTRY=registry.corp.internal

# Verify
kubectl -n monitoring get pods
```

## What `make corp-deploy` does

1. `corp-sync-images.sh` reads `versions.yaml`, pulls images from GHCR/DockerHub, pushes to corp registry
2. `helmfile -e corp diff` previews changes
3. `helmfile -e corp sync` deploys to the cluster

## Rollback

```bash
# Single release
helm -n monitoring rollback <release-name> <revision>

# Full rollback to a previous version
git checkout v1.1.0
make corp-deploy CORP_REGISTRY=registry.corp.internal
```
