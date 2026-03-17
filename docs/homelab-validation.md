# Homelab Deployment Validation

Manual check process after deploying to the homelab K8s cluster.

## 1. Pre-deploy

```bash
kubectl cluster-info
kubectl get nodes
make homelab-diff          # preview Helm changes
```

## 2. Deploy

```bash
make homelab-sync
```

## 3. Automated validation

```bash
make validate
```

This runs `scripts/validate-deployment.sh`, which checks:
- All pods in the `monitoring` namespace are `Running`
- vminsert and vmselect health endpoints
- vmagent health endpoint
- Grafana health endpoint

If all checks pass, the basic stack is healthy. The steps below provide deeper verification.

## 4. Metrics pipeline

Verify that mock GPU metrics flow through vmagent into VictoriaMetrics.

```bash
kubectl -n monitoring port-forward deploy/vmagent-central-vmagent-central 8429:8429 &
kubectl -n monitoring port-forward svc/victoriametrics-victoria-metrics-cluster-vmselect 8481:8481 &
```

### Check vmagent scrape targets

```bash
curl -s http://127.0.0.1:8429/api/v1/targets | python3 -m json.tool | grep -A2 '"health"'
```

All targets should show `"health": "up"`.

### Query GPU metrics

```bash
curl -s 'http://127.0.0.1:8481/select/0/prometheus/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL'
```

Expect a non-empty `result` array with mock GPU utilization values.

## 5. ClickHouse

```bash
kubectl -n clickhouse port-forward svc/clickhouse-gpu-monitoring 8123:8123 &
```

### Check schema

```bash
curl -s 'http://127.0.0.1:8123/?query=SHOW+TABLES+FROM+gpu_monitoring'
```

Expect at least: `gpu_unified_logs`, `s2_jobs`.

### Check table is queryable

```bash
curl -s 'http://127.0.0.1:8123/?query=SELECT+count()+FROM+gpu_monitoring.gpu_unified_logs'
```

## 6. Vector aggregator

```bash
kubectl -n monitoring logs -l app.kubernetes.io/name=vector --tail=30
```

Check for errors in the log output. Healthy Vector logs show periodic internal metrics with no error-level entries.

## 7. Grafana datasources

```bash
kubectl -n monitoring port-forward svc/grafana 3000:3000 &
curl -s -u admin:admin http://127.0.0.1:3000/api/datasources | python3 -m json.tool
```

Verify both VictoriaMetrics (Prometheus type) and ClickHouse datasources are listed.

## 8. Label verification

Confirm taxonomy labels are applied to all metrics:

```bash
curl -s 'http://127.0.0.1:8481/select/0/prometheus/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL' \
  | python3 -c "
import sys, json
m = json.load(sys.stdin)['data']['result'][0]['metric']
for k in ('deployment_env', 'platform', 'cluster'):
    print(f'  {k}={m.get(k, \"MISSING\")}')"
```

Expected output:

```
  deployment_env=homelab
  platform=k8s
  cluster=homelab-cluster
```

## 9. Cleanup

Kill all background port-forwards:

```bash
pkill -f port-forward
```

## Quick reference

| Step | Command | Pass criteria |
|------|---------|---------------|
| Pods + health | `make validate` | All checks pass |
| Scrape targets | vmagent `/api/v1/targets` | All targets UP |
| GPU metrics | Query `DCGM_FI_DEV_GPU_UTIL` | Non-empty result |
| ClickHouse | `SHOW TABLES FROM gpu_monitoring` | Tables present |
| Grafana DS | `/api/datasources` | 2 datasources listed |
| Labels | Check metric labels | `deployment_env=homelab, platform=k8s` |

Steps 1-3 are the **minimum** for every deployment. Steps 4-8 are recommended when changing the metrics pipeline, ClickHouse schema, or label taxonomy.
