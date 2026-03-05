---
name: gpu-mon-pr-review
description: Review pull requests in the gpu-mon repository. Use this for PRs that touch Helmfile, Helm charts, Docker Compose, Ansible, Python services, schemas, dashboards, or deployment docs, especially when the goal is to find bugs, regressions, operational risks, and missing tests.
---

# GPU Mon PR Review

Use this skill for repository-specific PR review work.

## Review Goal

Find defects and deployment risks before style issues. The primary output is a findings-first review with concise evidence.

## Branch Policy

- Use `dev` as the default review base branch.
- Allow exceptions only when the request explicitly targets `hotfix/*`, `release/*`, or another named base branch.
- Assume PRs merge into `dev` first.
- Treat `dev` as the branch used to test, debug, and validate execution on homelab and production-oriented environments before promotion to `main`.
- Include `main` mergeability as a secondary check when a change introduces temporary debug logic, environment-specific behavior, or validation-only code.

## Review Workflow

1. Start from the diff against `dev`, not assumptions. Inspect changed files, diff stats, and branch context first.
2. Classify the PR by layer:
   - platform config: `helmfile.yaml`, `environments/`, `compose/`
   - deployment automation: `ansible/`, `scripts/`
   - application logic: `src/`
   - data contract: `schemas/`, dashboard inputs, alert rules
   - docs: `README.md`, `deployment-workflow.md`
3. For each touched layer, ask:
   - Can this break existing deployment paths?
   - Does this change a public/private boundary?
   - Does this change an observability contract?
   - Is there test coverage at the right level?
4. Verify documentation drift whenever commands, paths, or operating assumptions change.
5. Prefer concrete findings with file references over speculative commentary.

## Repository Heuristics

- This is a public base repo. Company-only values, credentials, cluster names, inventory details, registries, and internal docs must stay out.
- Shared behavior belongs in base environments and reusable charts, not in one-off overlays.
- Pull metrics, push logs, and storage schemas form cross-component contracts. Review changes to names, labels, ports, and table fields as compatibility risks.
- Deployment changes should be evaluated for both local iteration (`compose/`) and cluster deployment (`helmfile.yaml`, `ansible/`) when relevant.
- Because `dev` is the validation branch, temporary instrumentation or debug-oriented changes need an explicit cleanup or promotion plan before eventual merge to `main`.

## What To Flag

- Secret leakage, internal-only data, or production assumptions committed into the public repo
- Helm or environment changes that silently alter rendered manifests or release wiring
- Compose, script, or Ansible changes that create non-idempotent or host-specific behavior
- Python changes lacking tests for parsing, scheduling, retries, or external API failure cases
- Schema changes without downstream query or dashboard impact analysis
- Docs that no longer match actual commands or file locations

## Output Format

Return findings first, ordered by severity, each with:

- short title
- why it matters
- file reference
- exact validation command/output summary, or a clear reason it was not validated
- missing test or validation angle if applicable

After findings, include:

- open questions or assumptions
- one short summary of overall risk
