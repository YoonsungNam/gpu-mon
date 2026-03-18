# Corp Configuration Overlay — Risk Analysis & Remediation Plan

> Risk review of the gpu-mon / gpu-mon-corp two-repo overlay strategy.
> Goal: maximize reuse of the public gpu-mon repo while keeping corp-specific
> changes isolated in the private gpu-mon-corp repo.

## Current Strategy

```
gpu-mon (public)                    gpu-mon-corp (private)
├── helmfile.yaml.gotmpl            ├── environments/corp/
├── environments/defaults.yaml      │   ├── values.yaml
├── environments/homelab/           │   ├── vmagent.yaml
├── environments/corp.example/      │   ├── victoriametrics.yaml
├── charts/                         │   ├── clickhouse.yaml
├── ansible/                        │   ├── grafana.yaml
├── scripts/                        │   ├── vector.yaml
└── ...                             │   ├── metadata-collector.yaml
                                    │   └── targets/*.json
     symlink bridge ◄───────────────┤
     environments/corp/ → ../../gpu-mon-corp/environments/corp/
                                    ├── ansible/inventory/corp.ini
                                    └── alerting/alertmanager/corp.yaml
```

**Mechanism**: Symlinks connect gitignored paths in gpu-mon to real files in
gpu-mon-corp. Helmfile resolves `environments/{{ .Environment.Name }}/` at
render time, pulling the correct values per environment.

## Current State of gpu-mon-corp

gpu-mon-corp already has **all 7 required values files** plus File SD targets,
Ansible inventory, and Alertmanager config:

| File | gpu-mon-corp | corp.example |
|---|---|---|
| `values.yaml` | Exists | Exists |
| `vmagent.yaml` | Exists | Exists |
| `victoriametrics.yaml` | Exists | **Missing** |
| `clickhouse.yaml` | Exists | **Missing** |
| `grafana.yaml` | Exists | **Missing** |
| `vector.yaml` | Exists | **Missing** |
| `metadata-collector.yaml` | Exists | **Missing** |
| `targets/baremetal-gpu-nodes.json` | Exists | Exists |
| `targets/vm-gpu-nodes.json` | Exists | N/A |
| `targets/inference-servers.json` | Exists | N/A |

**Key finding**: Corp deploys are not broken today. The risks are about
maintainability — the public repo's documentation and CI don't fully reflect
the contract that gpu-mon-corp must satisfy.

---

## Risk Inventory

### R1. Incomplete corp.example templates (Medium — downgraded from High)

**Problem**: Helmfile references 7 per-environment values files, but
corp.example only provides 2 of them. gpu-mon-corp already has all 7 files,
so this is not a deploy blocker — but it is a documentation gap.

**Impact**: New team members setting up gpu-mon-corp from scratch don't know
the full set of required files. The corp.example directory understates the
actual configuration surface.

**Remediation**: Add `.example` stubs for every file helmfile references.
Each stub should contain only the keys that differ from defaults, with
placeholder values and comments explaining what to fill in.

---

### R2. Chart version coupling in .tgz filenames (High)

**Problem**: Helmfile hardcodes chart versions in airgap `.tgz` paths:

```gotmpl
chart: "{{ .Values.charts_dir }}/victoria-metrics-cluster-0.36.0.tgz"
```

When gpu-mon bumps a chart version (e.g., 0.36.0 → 0.37.0), the `.tgz`
filename changes. The airgap bundle and corp server's `/opt/gpu-mon/charts/`
must be updated in lock-step — but there's no validation that the file exists.

**Impact**: Version bump in gpu-mon silently breaks corp deploy until the
bundle is also regenerated.

**Remediation**: Introduce `versions.yaml` as the SSOT (already planned as
D24). Helmfile reads chart versions from this file instead of inline literals.
The airgap bundle script reads the same file to download matching `.tgz` files.

---

### R3. No contract validation between repos (High)

**Problem**: When gpu-mon adds a new required values key, renames a chart
value, or changes a service endpoint, there is no automated check that
gpu-mon-corp's values files remain compatible.

**Impact**: gpu-mon updates can silently break corp deploys. Detected only at
deploy time on the corp cluster, which has limited debugging access.

**Remediation**: Add a CI job in gpu-mon that runs
`helmfile -e corp template` using the corp.example files (renamed without
`.example` suffix) as a smoke test. This catches missing keys, type
mismatches, and template render errors on every PR.

---

### R4. Retention settings split across two layers (Low — downgraded from Medium)

**Problem**: Retention is configured at two layers that could drift apart:

- **Helmfile-level**: `defaults.yaml` defines `retention.metrics_days: 90`,
  `retention.logs_days: 30`, `retention.metadata_days: 180`. These feed into
  ClickHouse TTL and Vector sink configs.
- **Chart-level**: VictoriaMetrics `retentionPeriod` is set directly in the
  per-environment `victoriametrics.yaml` files (homelab: `30d`, corp: `90d`).

Homelab already overrides VictoriaMetrics retention to `30d` in
`environments/homelab/victoriametrics.yaml`, so there is no immediate storage
waste. However, the Helmfile-level `retention.metrics_days: 90` in
`defaults.yaml` does not match homelab's actual `30d` — this inconsistency
could cause confusion if future charts or scripts consume the Helmfile value.

**Impact**: Low — no functional issue today. Risk is semantic drift between
the two retention settings if new components rely on `retention.metrics_days`.

**Remediation**: Align `retention.metrics_days` with the actual
`retentionPeriod` per environment. Either:
- (a) Override `retention.metrics_days: 30` in `environments/homelab/values.yaml`
  to match the VictoriaMetrics setting, or
- (b) Have the VictoriaMetrics chart read from `{{ .Values.retention.metrics_days }}`
  so there is a single source of truth.

Option (a) is simpler and sufficient for Phase 1.

---

### R5. No symlink pre-flight validation (Medium)

**Problem**: No Makefile target or script checks that `environments/corp/`
exists and points to a valid directory with all required files before running
`helmfile -e corp sync`.

**Impact**: A broken or missing symlink produces a cryptic helmfile
template-rendering error. Confusing for anyone setting up the environment for
the first time.

**Note**: gpu-mon-corp already has `scripts/setup-symlinks.sh` which creates
all required symlinks. The gap is on the gpu-mon side — no validation that
the symlinks are in place before deploy.

**Remediation**: Add a pre-flight check in the `corp-sync` / `corp-deploy`
Makefile targets that validates:
1. `environments/corp/` symlink exists and resolves
2. All required values files are present
3. Print a clear error message listing any missing files

---

### R6. Ansible lacks per-environment variable overrides (Medium)

**Problem**: Ansible playbooks define versions and URLs as inline vars:

```yaml
vars:
  dcgm_version: "3.3.5"
  vector_version: "0.50.0"
```

Corp may need different versions (newer DCGM, airgap download URLs).
There is no `group_vars/` or per-environment variable file to override these
without editing the shared playbook.

**Impact**: Corp-specific Ansible changes require forking the playbook or
maintaining patches in gpu-mon-corp that drift over time.

**Remediation**: Extract playbook vars into `ansible/vars/defaults.yaml`.
Add `ansible/vars/corp.yaml.example` with override instructions. Load via:

```bash
ansible-playbook -i inventory/corp.ini \
  -e @vars/defaults.yaml -e @vars/corp.yaml \
  playbooks/deploy-node-agents.yaml
```

**gpu-mon-corp change required**: Add `ansible/vars/corp.yaml` with
corp-specific version overrides. Update `scripts/setup-symlinks.sh` to
create the new symlink path.

---

### R7. Gitignore gaps for future corp paths (Low)

**Problem**: `.gitignore` covers three specific paths:

```
environments/corp/
ansible/inventory/corp*
alerting/alertmanager/corp*
```

If a new area needs corp-specific files (e.g., `dashboards/corp/`,
`schemas/corp-*.sql`), a developer could accidentally commit them before
remembering to update `.gitignore`.

**Note**: gpu-mon-corp's `docs/2-repo-plan.md` shows `**/corp*` as a
gitignore pattern, but this catch-all does not exist in gpu-mon's actual
`.gitignore`.

**Impact**: Accidental corp data exposure in a public repo.

**Remediation**: Add a broad catch-all pattern `**/corp/` to `.gitignore`.
This covers any future subdirectory without requiring per-path additions.

**gpu-mon-corp change required**: Update `docs/2-repo-plan.md` to match
the actual `.gitignore` patterns after the fix.

---

### R8. Compose and Helmfile config duplication (Low)

**Problem**: Docker Compose configs in `compose/` duplicate service
configurations (ports, env vars, image versions) that also appear in Helmfile
values. Changes to defaults in one context don't propagate to the other.

**Impact**: Config drift between macbook (compose) and homelab/corp (helmfile).
Dev testing may pass with stale compose values while helmfile values have moved.

**Remediation**: Acceptable for Phase 1 since compose is dev-only. When
`versions.yaml` is implemented (R2), compose can also read from it to keep
image versions in sync.

---

## Remediation Plan — Sequenced PRs

Each PR is scoped to one concern, reviewable in a single pass.
Changes are split by repo: most PRs only touch gpu-mon (public).
gpu-mon-corp changes are called out explicitly.

### PR 1: Complete corp.example templates (addresses R1) — gpu-mon only

**Files touched**:
- `environments/corp.example/victoriametrics.yaml.example` (new)
- `environments/corp.example/clickhouse.yaml.example` (new)
- `environments/corp.example/grafana.yaml.example` (new)
- `environments/corp.example/vector.yaml.example` (new)
- `environments/corp.example/metadata-collector.yaml.example` (new)
- `environments/corp.example/README.md` (update file table)

**Approach**: Use homelab values as a starting point, replace environment-specific
values with `YOUR_*_HERE` placeholders, add inline comments for each key.

**Estimated diff**: ~150 lines

---

### PR 2: Add CI smoke test for corp template rendering (addresses R3) — gpu-mon only

**Files touched**:
- `.github/workflows/helm-tests.yml` (add corp-template-render job)

**Approach**: CI job copies `*.example` → `*` in `environments/corp.example/`,
symlinks to `environments/corp/`, runs `helmfile -e corp template > /dev/null`.
Catches missing keys and template errors on every PR.

**Estimated diff**: ~40 lines

---

### PR 3: Add corp deploy pre-flight check (addresses R5) — gpu-mon only

**Files touched**:
- `scripts/validate-corp-setup.sh` (new)
- `Makefile` (add pre-flight call to corp-deploy target)

**Approach**: Script checks symlink existence, lists missing files vs. required
set, exits non-zero with actionable error messages.

**Estimated diff**: ~60 lines

---

### PR 4: Align Helmfile retention values with chart settings (addresses R4) — gpu-mon only

**Files touched**:
- `environments/homelab/values.yaml` (add `retention.metrics_days: 30` to
  match the `retentionPeriod: "30d"` already set in `victoriametrics.yaml`)

**Approach**: Add explicit retention override so the Helmfile-level value
matches the chart-level setting. Prevents semantic drift if future components
consume `retention.metrics_days`.

**Estimated diff**: ~10 lines

---

### PR 5: Broaden corp gitignore pattern (addresses R7) — both repos

**gpu-mon files touched**:
- `.gitignore` (add `**/corp/` catch-all)

**gpu-mon-corp files touched**:
- `docs/2-repo-plan.md` (update gitignore example to match actual patterns)

**Approach**: Add a single line. Verify existing specific patterns still work
(they do — gitignore applies all matching patterns).

**Estimated diff**: ~5 lines (gpu-mon) + ~5 lines (gpu-mon-corp)

---

### PR 6: Ansible per-environment variable files (addresses R6) — both repos

**gpu-mon files touched**:
- `ansible/vars/defaults.yaml` (new, extracted from playbook vars)
- `ansible/vars/corp.yaml.example` (new)
- `ansible/playbooks/deploy-node-agents.yaml` (remove inline vars, document
  `-e @vars/` usage)

**gpu-mon-corp files touched**:
- `ansible/vars/corp.yaml` (new, corp-specific version overrides)
- `scripts/setup-symlinks.sh` (add new symlink for `ansible/vars/corp.yaml`)

**Estimated diff**: ~80 lines (gpu-mon) + ~30 lines (gpu-mon-corp)

---

### PR 7: versions.yaml SSOT and helmfile integration (addresses R2) — gpu-mon only

**Files touched**:
- `versions.yaml` (new — already designed in corp-deployment-strategy.md)
- `helmfile.yaml.gotmpl` (read chart versions from `.Values`)
- `environments/defaults.yaml` (add chart version references)

**Approach**: This is the largest change and depends on PR 1-2 being merged
first (so corp.example templates exist for CI validation). Implement the
`versions.yaml` design from D24 in corp-deployment-strategy.md.

**Estimated diff**: ~120 lines

---

## Cross-Repo Impact Summary

| PR | gpu-mon | gpu-mon-corp |
|---|---|---|
| PR 1 | corp.example stubs | No change needed |
| PR 2 | CI smoke test | No change needed |
| PR 3 | Pre-flight check | No change needed |
| PR 4 | Homelab retention | No change needed |
| PR 5 | Gitignore catch-all | Update 2-repo-plan.md |
| PR 6 | Ansible vars extraction | Add vars/corp.yaml + update setup-symlinks.sh |
| PR 7 | versions.yaml SSOT | No change needed |

**5 of 7 PRs are gpu-mon only.** Only PR 5 and PR 6 require matching changes
in gpu-mon-corp, and those changes are small (doc update + new vars file).

---

## Priority Matrix

| PR | Risk | Severity | Effort | Dependencies |
|---|---|---|---|---|
| PR 1 | R1 — Incomplete corp.example | Medium | Low | None |
| PR 2 | R3 — No contract validation | High | Low | PR 1 |
| PR 3 | R5 — No pre-flight check | Medium | Low | None |
| PR 4 | R4 — Retention value drift | Low | Trivial | None |
| PR 5 | R7 — Gitignore gaps | Low | Trivial | None |
| PR 6 | R6 — Ansible env vars | Medium | Medium | None |
| PR 7 | R2 — Chart version coupling | High | Medium | PR 1, PR 2 |

**Recommended order**: PR 1 → PR 2 → (PR 3, PR 4, PR 5 in parallel) → PR 6 → PR 7

---

## Out of Scope

- **R8 (compose/helmfile duplication)**: Acceptable for Phase 1. Revisit when
  `versions.yaml` lands (PR 7) — compose can then read image versions from it.
- **Umbrella chart**: Deferred to Phase 3 per D22 in corp-deployment-strategy.md.
- **Skopeo migration**: Deferred to Phase 2 per D20.
