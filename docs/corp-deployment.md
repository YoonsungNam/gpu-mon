# Corp / Production Deployment Guide

This guide covers deploying gpu-mon to an air-gapped corp environment.
Actual infrastructure details live in the separate private `gpu-mon-corp` repo.

## Prerequisites

- `gpu-mon-corp` repo cloned alongside this repo
- Symlinks configured (see below)
- WSL with internet access (for bundle generation)

## One-time symlink setup (WSL)

```bash
cd ~/work/gpu-mon/environments/
ln -s ../../gpu-mon-corp/environments/corp ./corp

cd ~/work/gpu-mon/ansible/inventory/
ln -s ../../../gpu-mon-corp/ansible/inventory/corp.ini ./corp.ini

cd ~/work/gpu-mon/alerting/alertmanager/
ln -s ../../../gpu-mon-corp/alerting/alertmanager/corp.yaml ./corp.yaml
```

## Scenario A: Direct Helmfile deploy (intranet access)

```bash
cd ~/work/gpu-mon

# Pull latest
git pull
cd ../gpu-mon-corp && git pull && cd ../gpu-mon

# Preview changes
helmfile -e corp diff

# Deploy
helmfile -e corp sync

# Verify
kubectl -n monitoring get pods
```

## Scenario B: Airgap bundle

When the corp K8s cluster has no internet access:

```bash
# On WSL (internet available):
cd ~/work/gpu-mon
make corp-bundle
# → gpu-mon-airgap-YYYYMMDD-HHMMSS.tar.gz

# Transfer bundle to corp server, then on corp server:
tar xzf gpu-mon-airgap-*.tar.gz
./install.sh registry.internal.corp.com
```

## corp.example/ template

`environments/corp.example/` shows the structure without real values.
Copy to the private repo and fill in actual infrastructure details.
