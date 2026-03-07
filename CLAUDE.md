# GPU Monitoring System — Claude Code Context

## Project Overview
Unified GPU monitoring platform for multi-cluster environments (Baremetal + K8s + VMware VM).
Collects GPU metrics at 3 depth levels (L1 hardware → L2 efficiency → L3 kernel profiling).

## Architecture Summary
- **Metrics**: Pull-based — Central vmagent scrapes all node exporters
- **Logs**: Push-based — Lightweight Vector agents forward to central aggregator
- **Storage**: VictoriaMetrics (time-series) + ClickHouse (logs/metadata/profiling)
- **Metadata**: Optional integration with batch schedulers and VMware vCenter
- **Orchestration**: Helmfile for multi-environment management

## Repo Rules
- **Language**: All code, comments, docs, and commit messages in English
- **OSS Helm charts**: Referenced via Helmfile (never copied into this repo)
- **Custom Helm charts**: Only in `charts/` (for components without OSS charts)
- **Application source**: Only in `src/`
- **Environment configs**: Only in `environments/`
- **ClickHouse schemas**: Only in `schemas/`
- **No company-specific data**: Production configs live in a separate private repo
- **Corp paths are gitignored**: `environments/corp/`, `**/corp*` — never commit these

## Environments
- **macbook**: Docker Compose (no K8s), mock GPU metrics — for rapid development
- **homelab**: Helmfile + K8s, mock GPU metrics — for integration testing

## Current Phase
Phase 1: Foundation (L1+L2 metric pipeline)

## Tech Stack
- Helmfile, Helm 3
- VictoriaMetrics, ClickHouse, Grafana, Vector, vmagent
- Python (metadata-collector, mock-exporter)
- Ansible (baremetal/VM node agent deployment)

## Key Design Decisions
- D1: vmagent over Prometheus (VM ecosystem, lightweight)
- D2: Vector over Fluent Bit (native ClickHouse sink, VRL transforms)
- D15: DCGM Profiling ↔ CUPTI conflict managed via L2 pause/resume protocol
- D18: Hybrid Pull (metrics) + Push (logs) — see docs/planning-v5.md
- D19: File-based Service Discovery (Ansible-managed JSON)

## Key Reference Documents (read before major changes)
- `docs/planning-v5.md` — Full system architecture design document (v5, English)
- `docs/planning-v5.ko.md` — Same document in Korean (original)

## Branch Strategy
- `dev` is the integration branch — all feature branches branch from and merge into `dev`
- `main` is the promotion target after validation on `dev`
- PRs target `dev` unless explicitly `hotfix/*` or `release/*`

## Code Generation Rules
- **Small, focused changes only.** Each PR must touch one logical concern. If a task spans multiple layers (e.g. chart + Python + docs), split into separate PRs per layer.
- **Maximum diff guideline:** Aim for under ~200 lines changed per PR. If a generated change exceeds this, break it into sequential PRs with clear dependency order.
- **No speculative additions.** Only generate code or config that is directly requested. Do not add "while we're here" improvements, extra utilities, or premature abstractions.
- **Respect existing structure.** Follow the repo layout in Repo Rules above. Never create new top-level directories or parallel conventions without explicit approval.
- **No secrets or corp data.** Never generate content containing credentials, internal hostnames, registries, or company-specific configuration.

## PR Review Rules
- Review against `dev` as base branch by default.
- Prioritize correctness over style — focus on bugs, regressions, unsafe defaults, and missing validation before cosmetic issues.
- Review by deployment layer — treat Docker Compose, Helmfile, Helm charts, Ansible, Python services, schemas, and docs as separate failure domains.
- Flag changes that break observability contracts (metric names, labels, ports, log formats, schema fields).
- Flag changes that violate public/private separation.
- Require test evidence for runtime-affecting changes in `src/`, `charts/`, `environments/`, `ansible/`, `scripts/`, `compose/`, `schemas/`. Allow skipping only for docs/comments-only changes with explicit rationale.
- If tests cannot run in the current environment, mark as unverified with the blocking reason.
- Output findings first, ordered by severity, each with file reference and validation evidence.

## Change Scope Discipline
- When prototyping, propose a plan of small incremental PRs before generating code.
- Each PR should be reviewable in a single pass — if you need to scroll extensively, the PR is too large.
- Prefer additive changes (new files, new sections) over wide refactors across many existing files.
- If a refactor is necessary, isolate it in a dedicated PR with no functional changes mixed in.
- Always list which files will be touched before making changes, so the scope is clear upfront.
