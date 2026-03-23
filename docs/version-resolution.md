# Version Resolution

This document describes how versioned values are resolved today in `gpu-mon`,
with emphasis on image tags, Helm chart versions, and corp deployment paths.

## Precedence Order

For Helmfile-managed environments, values are loaded in this order:

1. `versions.yaml`
2. `environments/defaults.yaml`
3. `environments/<env>/values.yaml`

Later files override earlier files when they use the same key.

Current source: [helmfile.yaml.gotmpl](../helmfile.yaml.gotmpl).

## Current Behavior

| Item | Default Source | Used By | Environment Override | Example |
|---|---|---|---|---|
| OSS image tags | `versions.yaml -> oss_images` | `scripts/corp-sync-images.sh`, `scripts/airgap-bundle.sh` | Not standardized per-image today | `victoriametrics/vmagent: v1.106.1` |
| Custom image tags for sync | `versions.yaml -> custom_images` | `scripts/corp-sync-images.sh` | Only by overriding the same key | `custom_images.metadata-collector: v1.0.0` |
| Custom image tags for airgap bundle | `versions.yaml -> custom_images` | `scripts/airgap-bundle.sh` | Only by overriding the same key | Bundle includes `ghcr.io/yoonsungnam/gpu-mon/metadata-collector:v1.0.0` |
| `metadata-collector` deploy tag | `versions.yaml -> custom_images.metadata-collector` | `helmfile.yaml.gotmpl` injects the tag into `charts/metadata-collector` | Yes, override `custom_images.metadata-collector` in `environments/<env>/values.yaml` | Corp can deploy `v1.0.0` by default or `v1.0.1-corp` if overridden |
| `grafana-plugins` init-container tag | `versions.yaml -> custom_images.grafana-plugins` | `helmfile.yaml.gotmpl` injects the tag when `grafana_plugins_init: true` | Yes, override `custom_images.grafana-plugins` in `environments/<env>/values.yaml` | Corp can deploy `grafana-plugins:main` by default |
| Grafana plugin versions | `versions.yaml -> grafana_plugins` | `scripts/build-grafana-plugins.sh`; pinned install lists in Compose/homelab/corp examples | Not currently overridden per environment | `grafana_plugins.victoriametrics-metrics-datasource: 0.22.0` |
| `mock-dcgm-exporter` deploy tag | `environments/<env>/values.yaml -> image_tag` | `helmfile.yaml.gotmpl` injects the tag into `charts/mock-dcgm-exporter` | Yes, via `image_tag` | Homelab uses `image_tag: dev` |
| Helm chart versions | `versions.yaml -> helm_charts` | `helmfile.yaml.gotmpl` | Yes, override the same `helm_charts.<name>` key | `helm_charts.grafana: 10.5.15` |
| Tool versions | `versions.yaml -> tools` | `scripts/airgap-bundle.sh` | Not currently overridden per environment | `tools.helmfile: v0.169.2` |

## Examples

### Example 1: Default from `versions.yaml`

```yaml
# versions.yaml
custom_images:
  metadata-collector: v1.1.0
```

Result:

- `scripts/corp-sync-images.sh` mirrors `ghcr.io/yoonsungnam/gpu-mon/metadata-collector:v1.1.0`
- `scripts/airgap-bundle.sh` includes `ghcr.io/yoonsungnam/gpu-mon/metadata-collector:v1.1.0`
- Corp Helm deploy uses `metadata-collector:v1.1.0`

### Example 2: Environment override of the same key

```yaml
# versions.yaml
custom_images:
  metadata-collector: v1.1.0
```

```yaml
# environments/corp/values.yaml
custom_images:
  metadata-collector: v1.1.1-corp-hotfix
```

Result:

- Corp image sync uses `v1.1.1-corp-hotfix`
- Corp airgap bundle uses `v1.1.1-corp-hotfix`
- Corp Helm deploy uses `v1.1.1-corp-hotfix`

### Example 3: Current `mock-dcgm-exporter` behavior

```yaml
# environments/homelab/values.yaml
image_tag: dev
```

Result:

- `mock-dcgm-exporter` deploys with tag `dev`
- This path currently uses `image_tag`, not `custom_images.mock-dcgm-exporter`

### Example 4: Grafana plugin version bump

```yaml
# versions.yaml
custom_images:
  grafana-plugins: main

grafana_plugins:
  grafana-clickhouse-datasource: 4.14.0
  victoriametrics-metrics-datasource: 0.22.0
```

Result:

- `scripts/build-grafana-plugins.sh` bakes those plugin versions into the carrier image
- Corp deploy mirrors `ghcr.io/yoonsungnam/gpu-mon/grafana-plugins:main`
- If `grafana_plugins_init: true`, Helmfile injects that image as a Grafana init container
- Compose and homelab use the same pinned plugin versions in their Grafana install lists

## Recommended Pattern

Use one key per versioned thing:

- Helm chart versions: `helm_charts.<name>`
- Custom image tags: `custom_images.<name>`
- OSS image tags: `oss_images.<name>`
- Grafana plugin versions: `grafana_plugins.<plugin-id>`

Use `versions.yaml` for the baseline value, and override the same key in
`environments/<env>/values.yaml` only when that environment needs an exception.

Avoid parallel controls for the same thing, such as mixing:

- `versions.yaml`
- `image_tag`
- release-specific `image.tag` overrides

That pattern creates drift between sync, bundle, and deploy paths.
