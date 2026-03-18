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

---

## Risk Inventory

### R1. Incomplete corp.example templates (High)

**Problem**: Helmfile references 7 per-environment values files, but
corp.example only provides 2 of them:

| File helmfile expects | homelab | corp.example |
|---|---|---|
| `values.yaml` | Yes | Yes |
| `vmagent.yaml` | Yes | Yes |
| `victoriametrics.yaml` | Yes | **Missing** |
| `clickhouse.yaml` | Yes | **Missing** |
| `grafana.yaml` | Yes | **Missing** |
| `vector.yaml` | Yes | **Missing** |
| `metadata-collector.yaml` | N/A | **Missing** |

If gpu-mon-corp is missing any of these files, `helmfile -e corp template`
fails at render time with a file-not-found error.

**Impact**: Corp deploy fails silently on first attempt; new team members
setting up gpu-mon-corp don't know the full set of required files.

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

### R4. Production-grade defaults leak to dev environments (Medium)

**Problem**: `defaults.yaml` sets retention values appropriate for production:

```yaml
retention:
  metrics_days: 90
  logs_days: 30
  metadata_days: 180
```

Homelab's `values.yaml` does not override these, so the dev cluster inherits
90-day metric retention — wasting storage on a test environment.

**Impact**: Homelab storage fills up faster than expected; unclear whether
defaults are designed for dev or prod use.

**Remediation**: Either:
- (a) Set conservative dev defaults in `defaults.yaml` and override upward in
  corp, or
- (b) Override retention in `environments/homelab/values.yaml` to dev-appropriate
  values (e.g., 7d metrics, 7d logs, 30d metadata).

Option (b) is simpler and avoids changing defaults that corp already relies on.

---

### R5. No symlink pre-flight validation (Medium)

**Problem**: No Makefile target or script checks that `environments/corp/`
exists and points to a valid directory with all required files before running
`helmfile -e corp sync`.

**Impact**: A broken or missing symlink produces a cryptic helmfile
template-rendering error. Confusing for anyone setting up the environment for
the first time.

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

**Impact**: Accidental corp data exposure in a public repo.

**Remediation**: Add a broad catch-all pattern `**/corp/` to `.gitignore`.
This covers any future subdirectory without requiring per-path additions.

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

### PR 1: Complete corp.example templates (addresses R1)

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

### PR 2: Add CI smoke test for corp template rendering (addresses R3)

**Files touched**:
- `.github/workflows/helm-test.yaml` (add corp-template-render job)

**Approach**: CI job copies `*.example` → `*` in `environments/corp.example/`,
symlinks to `environments/corp/`, runs `helmfile -e corp template > /dev/null`.
Catches missing keys and template errors on every PR.

**Estimated diff**: ~40 lines

---

### PR 3: Add corp deploy pre-flight check (addresses R5)

**Files touched**:
- `scripts/validate-corp-setup.sh` (new)
- `Makefile` (add pre-flight call to corp-deploy target)

**Approach**: Script checks symlink existence, lists missing files vs. required
set, exits non-zero with actionable error messages.

**Estimated diff**: ~60 lines

---

### PR 4: Override homelab retention defaults (addresses R4)

**Files touched**:
- `environments/homelab/values.yaml` (add retention overrides)

**Approach**: Add dev-appropriate retention values (7d metrics, 7d logs, 30d
metadata) to homelab values.

**Estimated diff**: ~10 lines

---

### PR 5: Broaden corp gitignore pattern (addresses R7)

**Files touched**:
- `.gitignore` (add `**/corp/` catch-all)

**Approach**: Add a single line. Verify existing specific patterns still work
(they do — gitignore applies all matching patterns).

**Estimated diff**: ~5 lines

---

### PR 6: Ansible per-environment variable files (addresses R6)

**Files touched**:
- `ansible/vars/defaults.yaml` (new, extracted from playbook vars)
- `ansible/vars/corp.yaml.example` (new)
- `ansible/playbooks/deploy-node-agents.yaml` (remove inline vars, document
  `-e @vars/` usage)

**Estimated diff**: ~80 lines

---

### PR 7: versions.yaml SSOT and helmfile integration (addresses R2)

**Files touched**:
- `versions.yaml` (new — already designed in corp-deployment-strategy.md)
- `helmfile.yaml.gotmpl` (read chart versions from `.Values`)
- `environments/defaults.yaml` (add chart version references)

**Approach**: This is the largest change and depends on PR 1-2 being merged
first (so corp.example templates exist for CI validation). Implement the
`versions.yaml` design from D24 in corp-deployment-strategy.md.

**Estimated diff**: ~120 lines

---

## Priority Matrix

| PR | Risk | Severity | Effort | Dependencies |
|---|---|---|---|---|
| PR 1 | R1 — Missing corp.example | High | Low | None |
| PR 2 | R3 — No contract validation | High | Low | PR 1 |
| PR 3 | R5 — No pre-flight check | Medium | Low | None |
| PR 4 | R4 — Dev retention defaults | Medium | Trivial | None |
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
