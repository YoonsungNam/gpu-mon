# AGENTS.md

## Purpose

This repository is a public baseline for `gpu-mon`, a GPU observability platform spanning Docker Compose, Helmfile-managed Kubernetes, Ansible-managed node agents, and Python services.

Use this file when reviewing or changing code in this repo with Codex.

## Branch Strategy

- Review all pull requests against `dev`, not `main`, unless the user explicitly asks for a different comparison base.
- Allow base-branch exceptions only for explicit `hotfix/*` or `release/*` review requests.
- Treat `dev` as the integration branch for testing, debugging, and validating `gpu-mon` behavior in homelab and production-like execution paths.
- Treat `main` as the promotion target after changes have already been validated through `dev`.
- When reviewing, evaluate whether a change is safe for `dev` deployment and whether it preserves a clean eventual merge path from `dev` to `main`.

## Core Review Principles

1. Prioritize correctness over style. Focus first on bugs, regressions, unsafe defaults, and missing validation.
2. Review by deployment layer. Check Docker Compose, Helmfile, Helm charts, Ansible roles, Python services, schemas, and docs as separate failure domains.
3. Preserve public/private separation. Never introduce company-specific endpoints, inventories, credentials, registries, kubeconfigs, or internal runbooks into this repo.
4. Keep environment boundaries explicit. Shared defaults belong in `environments/defaults.yaml`; environment-specific overrides belong only under `environments/<env>/`.
5. Treat observability contracts as APIs. Metric names, labels, ports, scrape paths, log formats, schema fields, and dashboard inputs must remain stable unless intentionally versioned.
6. Require test impact analysis. Any change to Python logic, chart templating, scrape configs, log routing, or deployment automation should state what level of testing covers it.
7. Favor minimal blast radius. Prefer targeted changes over wide refactors, especially around deployment entrypoints such as `helmfile.yaml`, `compose/`, `scripts/`, and `ansible/`.
8. Verify operational failure modes. Check startup behavior, retries, missing env vars, empty inventories, unreachable endpoints, and partial deployments.
9. Keep docs aligned with executable paths. If a PR changes commands, file locations, or deployment flow, review the corresponding documentation in the same pass.
10. Call out what is unverified. If tests were not run or cannot run in the current environment, say so explicitly.

## Repository-Specific Review Checklist

- `README.md` and `deployment-workflow.md` remain consistent with the current architecture.
- Public repo constraints from `CLAUDE.md` are preserved:
  - English-only repository content
  - No copied OSS charts
  - No production or corporate-only configuration
- Helm and Helmfile changes maintain valid value flow across `helmfile.yaml`, `environments/`, and `charts/`.
- Ansible changes keep idempotent behavior and do not hardcode host-specific state.
- Python service changes preserve adapter boundaries, configuration loading, and failure handling.
- Schema or telemetry changes document downstream impact on dashboards, alerts, and queries.
- New paths follow the existing repository layout instead of introducing parallel conventions.
- PR diffs and review comments assume `dev` as the base branch and consider downstream mergeability into `main`.

## Conditional Test Gate

- Treat test execution as conditionally required, not optional, when runtime behavior can change.
- Run and report relevant checks for changes in `src/`, `helmfile.yaml`, `charts/`, `environments/`, `ansible/`, `scripts/`, `compose/`, and `schemas/`.
- For docs-only, comments-only, or metadata-only changes with no runtime impact, tests may be skipped with an explicit rationale.
- If a required check fails, raise a finding with severity based on impact and include the failing command/output summary.
- If a required check cannot run in the current environment, mark it as unverified, include the blocking reason, and describe deployment risk.

## Expected Review Output

When asked to review a PR in this repo, lead with findings in severity order and include file references. Keep the summary short. For each finding, include the validation evidence (exact command used) or explicitly state why it was not validated in the current environment. If there are no findings, state that clearly and list residual risks such as unrun tests or unverified deployment paths.
