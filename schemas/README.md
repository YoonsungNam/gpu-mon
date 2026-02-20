# ClickHouse Schemas

All DDL files for the `gpu_monitoring` database.

| File | Table | Engine | Purpose |
|---|---|---|---|
| `gpu_unified_logs.sql` | `gpu_unified_logs` | MergeTree | Normalized logs from all environments |
| `s2_jobs.sql` | `s2_jobs` | MergeTree | S2 batch scheduler job history (time-series) |
| `s2_nodes.sql` | `s2_nodes` | ReplacingMergeTree | S2 node current state |
| `s2_projects.sql` | `s2_projects` | ReplacingMergeTree | S2 project/FairShare config |
| `s2_pools.sql` | `s2_pools` | ReplacingMergeTree | S2 logical node pool config |
| `vmware_vm_inventory.sql` | `vmware_vm_inventory` | ReplacingMergeTree | VMware GPU VM inventory |

## Apply schemas

```bash
# From gpu-mon root, against a running ClickHouse instance:
for f in schemas/*.sql; do
  clickhouse-client --host localhost --query "$(cat $f)"
done
```

Replace `{cluster}` with your actual ClickHouse cluster name before applying in production.
