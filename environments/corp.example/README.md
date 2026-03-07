# Production Environment Template

This directory is a **template** showing how to configure gpu-mon for a production or air-gapped environment.

Actual production values live in a separate private repo and are symlinked here at deploy time. See [docs/extending-to-production.md](../../docs/extending-to-production.md) for the full guide.

## Files

| File | Purpose |
|---|---|
| `values.yaml.example` | Environment-level variables (registry, feature flags) |
| `vmagent.yaml.example` | Central vmagent scrape configuration overrides |
| `targets/gpu-nodes.json.example` | File SD target list for Baremetal/VM GPU nodes |

## Usage

```bash
# 1. Copy examples and fill in real values
cp values.yaml.example values.yaml
cp vmagent.yaml.example vmagent.yaml
cp targets/gpu-nodes.json.example targets/gpu-nodes.json

# 2. Edit with your actual infrastructure details
vim values.yaml

# 3. Deploy
helmfile -e corp sync
```
