# CI Image Management

This document describes how custom container images are built, tagged, and cleaned up in this repository.

## Overview

Custom images (e.g., `mock-dcgm-exporter`, `metadata-collector`) are built by GitHub Actions and pushed to GHCR (GitHub Container Registry) at `ghcr.io/yoonsungnam/gpu-mon/<image>`.

## Workflows

| Workflow | File | Trigger |
|---|---|---|
| Docker Build & Push | `.github/workflows/docker-build-push.yml` | Push to `dev`/`main` when `src/<image>/**` changes |
| Cleanup GHCR Images | `.github/workflows/cleanup-ghcr.yml` | Weekly (Sunday 00:00 UTC) + manual |

## Tagging Strategy

Each image build produces up to 3 tags:

| Tag | Example | Mutable | Updates on | Purpose |
|---|---|---|---|---|
| `<branch>-<short-sha>` | `dev-02bab9f` | No | Every push | Immutable reference for rollback and debugging |
| `<branch>` | `dev` | Yes | Every push | Tracks the latest build per branch |
| `latest` | `latest` | Yes | `main` push only | Stable/promoted image |

### Key design decisions

- **`:latest` only moves on `main`** — prevents confusion between integration and stable builds. The homelab environment uses the `:dev` tag instead.
- **`<branch>-<short-sha>` over full SHA** — 7-char prefix is human-readable while remaining unique enough for this repository's commit volume.
- **No timestamp in tags** — the short SHA already provides traceability via `git log`, and adding timestamps would make tags unnecessarily long.

## Image Lifecycle

```
Push to dev branch
  -> CI builds image
  -> Tags: dev-abc1234, dev

Push to main branch (promotion)
  -> CI builds image
  -> Tags: main-def5678, main, latest

Weekly cleanup
  -> Deletes old versions, keeps 5 most recent per image
  -> Preserves: latest, dev, main (never deleted)
```

## Cleanup Policy

The `cleanup-ghcr.yml` workflow runs weekly and uses `actions/delete-package-versions@v5` with:

- **`min-versions-to-keep: 5`** — retains the 5 most recent image versions
- **`ignore-versions: "^(latest|dev|main)$"`** — mutable tags are never pruned

This means at any point, you have access to:
- The current stable image (`:latest`, `:main`)
- The current integration image (`:dev`)
- Up to 5 recent immutable snapshots for rollback

## Environment Image Tags

Each environment uses a different image tag to match its role:

| Environment | `image_tag` | Rationale |
|---|---|---|
| homelab | `dev` | Tracks integration branch for testing |
| corp | Pinned (e.g., `v1.0.0`) | Explicit version control for production |

The `image_tag` value is set in `environments/<env>/values.yaml` and wired into charts via `helmfile.yaml.gotmpl`.

## Manual Operations

### Trigger a build manually

The build workflow does not support `workflow_dispatch`. To rebuild, push a no-op change to a source file on `dev` or `main`.

### Trigger cleanup manually

```bash
gh workflow run cleanup-ghcr.yml
```

### List existing image tags

```bash
gh api user/packages/container/gpu-mon%2Fmock-dcgm-exporter/versions \
  --jq '.[].metadata.container.tags[]'
```
