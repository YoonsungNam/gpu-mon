# Production Environment Template

This directory is a **template** showing how to configure gpu-mon for a production or air-gapped environment.

Actual production values live in a separate private repo and are symlinked here at deploy time. See [docs/extending-to-production.md](../../docs/extending-to-production.md) for the full guide.

## Files

| File | Purpose |
|---|---|
| `values.yaml.example` | Environment-level variables (registry, feature flags, retention) |
| `vmagent.yaml.example` | Central vmagent scrape configuration (File SD targets) |
| `victoriametrics.yaml.example` | VictoriaMetrics cluster sizing and retention |
| `clickhouse.yaml.example` | ClickHouse cluster layout, storage, and schema init |
| `grafana.yaml.example` | Grafana datasources, dashboards, ingress, and plugins |
| `vector.yaml.example` | Vector aggregator sources, transforms, and ClickHouse sink |
| `metadata-collector.yaml.example` | Metadata collector S2/VMware integration |
| `storageclass.yaml.example` | StorageClass for IBM Spectrum Scale CSI (apply before helmfile sync) |
| `targets/baremetal-gpu-nodes.json.example` | File SD target list for Baremetal GPU nodes |
| `targets/vm-gpu-nodes.json.example` | File SD target list for VMware GPU VMs |
| `targets/inference-servers.json.example` | File SD target list for inference servers |

All Helm values YAML files above are required for `helmfile -e corp sync`
to succeed (except `metadata-collector.yaml.example`, which is only needed
when `metadata_collector.enabled: true` in `values.yaml`).
`storageclass.yaml.example` is a separate cluster prerequisite used by
`make corp-deploy` and `make corp-sync` when the StorageClass is absent.
Target JSON files are required when the corresponding scrape jobs are
configured in `vmagent.yaml`.

## StorageClass lifecycle

`storageclass.yaml` defines a cluster-scoped StorageClass required by
VictoriaMetrics and ClickHouse PVCs. It is **not** managed by Helmfile.

`make corp-deploy` and `make corp-sync` handle it automatically:
- If the StorageClass already exists in the cluster, it is **skipped**
  (safe when the SC is managed externally, e.g. by the storage team).
- If it is absent and `environments/corp/storageclass.yaml` exists on disk,
  it is created via `kubectl apply --validate=false` to avoid blocking on OpenAPI schema fetch timeouts from the cluster API.
- To update an existing SC, delete it first then re-run:
  `kubectl delete sc spectrum-scale && make corp-deploy`

## Usage

```bash
# 1. Copy all examples and fill in real values
for f in *.example; do cp "$f" "${f%.example}"; done
for f in targets/*.example; do cp "$f" "${f%.example}"; done

# 2. Edit with your actual infrastructure details
vim values.yaml

# 3. Deploy (applies StorageClass if needed, then helmfile sync)
make corp-deploy
```
