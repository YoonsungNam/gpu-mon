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

cd ~/work/gpu-mon/ansible/vars/
ln -s ../../../gpu-mon-corp/ansible/vars/corp.yaml ./corp.yaml
# → Ansible variable overrides (agent versions, Vector endpoint, metadata labels)

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

## Grafana plugin carrier image

In corp, Grafana runs with `grafana_plugins_init: true`, so plugins are loaded
from the `grafana-plugins` carrier image instead of being downloaded from
grafana.com at pod startup.

CI automatically builds and pushes the `grafana-plugins` image to GHCR using
the tag from `versions.yaml -> custom_images.grafana-plugins` when either of
these change:

- `src/grafana-plugins/**` (Dockerfile)
- `versions.yaml` (plugin versions under `grafana_plugins`)

It also rebuilds when that configured GHCR tag is missing.

`make corp-deploy` then mirrors the image from GHCR into the corp registry
alongside all other custom images.

To build locally (e.g. for testing): `make build-grafana-plugins`

## What `make corp-deploy` does

1. Pre-flight validation (`validate-corp-setup.sh`) checks symlinks and required files, prints the current `kubectl` context/server, and warns if the kubeconfig appears to go through a Rancher proxy endpoint
2. StorageClass `spectrum-scale` is created if absent in the cluster (skipped if it already exists; fails if the manifest file is missing)
3. `corp-sync-images.sh` reads `versions.yaml`, pulls images from GHCR/DockerHub, and pushes them to the corp registry (includes the `grafana-plugins` carrier image when listed under `custom_images`)
4. `helmfile -e corp diff` previews changes
5. `./scripts/helmfile-sync.sh corp` runs the Helmfile sync with a narrow StatefulSet immutable-field recovery path

If `grafana_plugins_init: true` is set in `environments/corp/values.yaml`, the
Grafana release mounts an `emptyDir`, runs an init container from the
`grafana-plugins` image, and starts Grafana with `plugins: []` so no internet
download is required from the cluster.

## Helmfile sync retry flow

`make corp-deploy`, `make corp-sync`, and `make homelab-sync` all use
`./scripts/helmfile-sync.sh <environment>` instead of `helmfile sync` directly.
The wrapper auto-recovers from StatefulSet immutable field errors (e.g. PVC size
or storageClassName changes on vmstorage) by orphan-deleting only allowlisted
StatefulSets and retrying the sync. Pods and PVCs remain running throughout.

See [Helmfile Sync Retry Flow](helmfile-sync.md) for the full flow description,
safety guards, and flowchart.

## Rollback

```bash
# Single release
helm -n monitoring rollback <release-name> <revision>

# Full rollback to a previous version
git checkout v1.1.0
make corp-deploy CORP_REGISTRY=registry.corp.internal
```
