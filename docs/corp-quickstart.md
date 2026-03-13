# Corp Deployment Quickstart (Phase 1)

> Minimal steps to deploy gpu-mon to the airgapped corp cluster.
> For full design and Phase 2/3 evolution, see [corp-deployment-strategy.md](corp-deployment-strategy.md).

## Prerequisites

- Docker CLI + `yq` installed on WSL
- Authenticated to both GHCR and corp registry (`docker login`)
- `gpu-mon-corp` private repo cloned alongside this repo

### One-time symlink setup

The public `gpu-mon` repo contains all charts, source code, and environment-agnostic config.
Corp-specific details (registry URLs, node IPs, credentials, notification channels) live in the
separate **private** `gpu-mon-corp` repo to avoid leaking company-specific data.

Symlinks bridge the two repos so that tools like Helmfile and Ansible can find corp configs
at their expected paths without committing secrets to the public repo:

```bash
cd ~/work/gpu-mon/environments/
ln -s ../../gpu-mon-corp/environments/corp ./corp
# → Helmfile values for corp env (registry URL, image overrides, Helm values)

cd ~/work/gpu-mon/ansible/inventory/
ln -s ../../../gpu-mon-corp/ansible/inventory/corp.ini ./corp.ini
# → Ansible inventory (corp node IPs, SSH config for baremetal/VM agent deployment)

cd ~/work/gpu-mon/alerting/alertmanager/
ln -s ../../../gpu-mon-corp/alerting/alertmanager/corp.yaml ./corp.yaml
# → Alertmanager routing (corp notification channels, escalation rules)
```

These paths are gitignored (`environments/corp/`, `**/corp*`), so the symlinks
themselves are never committed. See `environments/corp.example/` for the expected
structure without real values.

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
