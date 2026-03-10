# Test Architecture

gpu-mon uses a four-layer test strategy. Each layer targets a different failure domain and has different infrastructure requirements.

## Test Layers

```
Layer 1  Unit          No dependencies        Every push        Fast (<30s)
Layer 2  Helm          Helm CLI only          Every push        Fast (<30s)
Layer 3  Component     Docker Compose stack   dev/main + PRs    Medium (~3min)
Layer 4  E2E           Docker Compose stack   dev/main + PRs    Medium (~5min)
```

### Layer 1 -- Unit Tests

Pure Python tests that validate business logic without external services.

| Scope | Directory | What it tests |
|-------|-----------|---------------|
| metadata-collector | `src/metadata-collector/tests/` | S2 adapter normalization, VMware adapter GPU detection, ClickHouse writer batching, scheduler timing |
| mock-dcgm-exporter | `src/mock-dcgm-exporter/tests/` | Metrics generation (L1/L2 DCGM names, label format, value ranges), HTTP handler (status codes, content types) |

**CI workflow**: `.github/workflows/unit-tests.yml`
- `Python unit tests` job: runs `pytest -m unit` across `src/metadata-collector/` and `tests/`
- `mock-dcgm-exporter unit tests` job: runs `pytest src/mock-dcgm-exporter/tests/` separately (isolated due to `tests/` namespace collision)

**Run locally**:
```bash
pip install -e ".[test]"
pytest -m unit -v
pytest src/mock-dcgm-exporter/tests/ -v
```

### Layer 2 -- Helm Chart Tests

Validates that Helm chart templates render correctly using [helm-unittest](https://github.com/helm-unittest/helm-unittest).

| Chart | Test directory | What it tests |
|-------|----------------|---------------|
| vmagent-central | `charts/vmagent-central/tests/` | Deployment, ConfigMap, Service, ServiceAccount rendering |
| mock-dcgm-exporter | `charts/mock-dcgm-exporter/tests/` | Deployment, Service rendering |
| metadata-collector | `charts/metadata-collector/tests/` | Deployment, ConfigMap rendering, secret injection |

**CI workflow**: `.github/workflows/helm-tests.yml`
- `helm lint` job: runs `helm lint` on all charts
- `helm-unittest` job: runs `helm unittest` on each chart

**Run locally**:
```bash
helm plugin install https://github.com/helm-unittest/helm-unittest
helm unittest charts/vmagent-central
helm unittest charts/mock-dcgm-exporter
helm unittest charts/metadata-collector
```

### Layer 3 -- Component Tests

Per-service smoke tests that verify each service in the Docker Compose stack is healthy, reachable, and correctly configured.

| Test file | What it validates |
|-----------|-------------------|
| `tests/component/test_victoriametrics.py` | vminsert/vmselect health, Prometheus remote write + query round-trip |
| `tests/component/test_clickhouse.py` | Schema presence, INSERT/SELECT round-trip |
| `tests/component/test_grafana.py` | Health, datasource provisioning, dashboard folder |
| `tests/component/test_vector.py` | TCP connectivity, ClickHouse sink dependency |
| `tests/component/test_vmagent.py` | Health, /api/v1/targets, mock-dcgm target UP |

**Requires**: Full Docker Compose stack (`make dev-up`)

**Run locally**:
```bash
make dev-up
pytest -m component -v
```

### Layer 4 -- E2E Pipeline Tests

End-to-end tests that verify data flows through the complete pipeline. Uses `poll_until` with timeouts to wait for asynchronous data propagation.

| Test file | Pipeline tested |
|-----------|-----------------|
| `tests/e2e/test_e2e_metrics.py` | mock-dcgm-exporter -> vmagent -> vminsert -> vmstorage -> vmselect query |
| `tests/e2e/test_e2e_logs.py` | Vector -> ClickHouse `gpu_unified_logs` row count |
| `tests/e2e/test_e2e_grafana.py` | Grafana proxy queries (PromQL via vmselect, SQL via ClickHouse) |

**Requires**: Full Docker Compose stack (`make dev-up`) with data flowing

**Run locally**:
```bash
make dev-up
# wait ~30s for metrics to propagate
pytest -m e2e -v
```

## CI Workflows Summary

| Workflow | Trigger | Jobs |
|----------|---------|------|
| `unit-tests.yml` | Every push + PR | Python unit tests, mock-dcgm unit tests |
| `helm-tests.yml` | Every push + PR | helm lint, helm-unittest |
| `lint.yml` | Every push + PR | ruff check, helm chart lint |
| `component-e2e-tests.yml` | Push to dev/main + PRs targeting dev/main | Docker Compose stack -> component tests -> E2E tests |
| `check-no-corp.yml` | Every PR | Ensures no corp-specific files are committed |

### Component + E2E in CI

The `component-e2e-tests.yml` workflow spins up the full Docker Compose stack on a GitHub Actions runner:

1. Build custom images (`make build-images`)
2. Start the stack (`make dev-up`)
3. Wait for services to be healthy (mock-exporter, vmselect, Grafana)
4. Run Layer 3 component tests
5. Run Layer 4 E2E tests
6. Dump service logs on failure
7. Tear down the stack

This tests the **macbook environment** (Docker Compose with mock GPU metrics), not a real GPU cluster. It validates that:
- Docker images build correctly
- All services start and interconnect
- Config files (vmagent scrape config, Vector pipeline, Grafana provisioning) work
- Data flows end-to-end through mock metrics

Real environment validation (homelab, corp) requires separate deployment-time smoke tests run in those environments.

## Shared Test Infrastructure

- **`tests/conftest.py`**: Shared fixtures, base URLs, `poll_until` helper, auto-skip logic for component/e2e when stack is not running
- **`pyproject.toml`**: pytest markers (`unit`, `component`, `e2e`, `ansible`), import mode (`importlib`), ruff config
- **Pytest markers**: Tests must be marked with `@pytest.mark.unit`, `@pytest.mark.component`, or `@pytest.mark.e2e`

## Adding New Tests

1. **Unit test**: Add to `src/<service>/tests/`, mark with `@pytest.mark.unit`
2. **Helm test**: Add to `charts/<chart>/tests/`, follow helm-unittest YAML format
3. **Component test**: Add to `tests/component/`, mark with `@pytest.mark.component`, import constants from `conftest`
4. **E2E test**: Add to `tests/e2e/`, mark with `@pytest.mark.e2e`, use `poll_until` for async assertions
