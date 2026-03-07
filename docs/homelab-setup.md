# Homelab Setup (K8s)

Deploy the full stack to a local K8s cluster (k3s, kind, etc.).

## Prerequisites

- K8s cluster with `kubectl` configured
- `helm` + `helmfile` + `helm-diff` plugin

```bash
./scripts/setup-homelab.sh
```

## Deploy

```bash
make homelab-diff    # preview changes
make homelab-sync    # deploy
```

## Verify

```bash
kubectl -n monitoring get pods
make validate
```

## Port-forward Grafana

```bash
kubectl -n monitoring port-forward svc/grafana 3000:3000
# open http://localhost:3000
```
