# ClickHouse Keeper — Migration to Helmfile Management

This runbook covers cutting over from a **manually deployed (kubectl-applied) single-node
ClickHouse Keeper** to the **Helmfile-managed 3-node `clickhouse-keeper` chart**
(`charts/clickhouse-keeper`, wired into `helmfile.yaml.gotmpl`).

It is written for a live cluster where:

- The old keeper runs as an ad-hoc `StatefulSet` + `ConfigMap` in the **`monitoring`** namespace
  (single replica, client port `2181`).
- The `ClickHouseInstallation` (CHI) currently references it via
  `zookeeper.nodes: [{host: clickhouse-keeper.monitoring.svc.cluster.local, port: 2181}]`.
- The new keeper is a fresh 3-node cluster in the **`clickhouse`** namespace
  (client port `9181`), enabled per environment via `keeper.yaml` and the CHI's
  `keeper.enabled` / `keeper.nodes` values.

> ⚠️ **This is a cutover, not an in-place upgrade.** The new keeper starts **empty**.
> ClickHouse stores its replication coordination metadata (replica registration, log
> pointers, part checksums) inside keeper. Pointing the CHI at an empty keeper makes all
> `Replicated*` tables **read-only** until that metadata is rebuilt. Schedule a maintenance
> window and stop writers first.

---

## 0. Prerequisites

- A maintenance window with **ingestion paused** (metadata-collector, ch-uploader, and any
  other writer to the ClickHouse cluster).
- `charts/clickhouse-keeper` chart merged (PR #66) and the corp/homelab `keeper.yaml`
  + CHI `keeper.enabled/keeper.nodes` values ready (corp PR #24).
- Confirm the keeper config binds to all interfaces (`<listen_host>0.0.0.0</listen_host>`)
  and the StatefulSet uses `podManagementPolicy: Parallel` — both required for the
  3-node raft quorum to form. (Already in the chart.)
- A backup or snapshot of the source of truth: list the replicated tables and their
  ClickHouse-side state before touching anything.

```sql
-- On any ClickHouse replica, capture the current replicated-table inventory:
SELECT database, table, replica_path, is_readonly
FROM system.replicas
ORDER BY database, table;
```

---

## 1. Deploy the new keeper (no CHI change yet)

Bring up the 3-node keeper **without** repointing ClickHouse. This lets you validate quorum
in isolation; the old keeper keeps serving ClickHouse the whole time.

```sh
helmfile -e <env> -l name=clickhouse-keeper apply
```

Verify all three pods are Running and a leader is elected:

```sh
kubectl -n clickhouse get pod -l app.kubernetes.io/name=clickhouse-keeper

# Quorum health via 4-letter words (ruok/mntr are allowed by default):
for i in 0 1 2; do
  echo "== keeper-$i =="
  kubectl -n clickhouse exec clickhouse-keeper-$i -- \
    sh -c 'echo ruok | timeout 2 clickhouse-keeper-client -q "" 2>/dev/null || echo ruok | (exec 3<>/dev/tcp/127.0.0.1/9181; cat >&3; cat <&3)'
done
# Expect "imok" from each. "mntr" shows zk_server_state=leader on exactly one node.
```

Do **not** proceed until exactly one `leader` and two `follower` nodes are reported.

---

## 2. Cutover — repoint the CHI at the new keeper

Inside the maintenance window, with writers stopped:

1. Enable keeper in the environment's CHI values (corp PR #24 already does this):

   ```yaml
   # environments/<env>/clickhouse.yaml
   keeper:
     enabled: true
     nodes:
       - host: clickhouse-keeper-0.clickhouse-keeper-headless.clickhouse.svc
         port: 9181
       - host: clickhouse-keeper-1.clickhouse-keeper-headless.clickhouse.svc
         port: 9181
       - host: clickhouse-keeper-2.clickhouse-keeper-headless.clickhouse.svc
         port: 9181
   ```

2. Apply the cluster release. The operator updates the CHI's `zookeeper.nodes` and rolls the
   ClickHouse pods to pick up the new coordination config:

   ```sh
   helmfile -e <env> -l name=clickhouse apply
   kubectl -n clickhouse rollout status statefulset -l clickhouse.altinity.com/chi
   ```

3. After the roll, the replicated tables will be **read-only** — expected, because the new
   keeper has none of their metadata yet:

   ```sql
   SELECT database, table, is_readonly FROM system.replicas WHERE is_readonly;
   ```

---

## 3. Rebuild replication metadata in the new keeper

For every replicated table, recreate its keeper-side metadata from the local parts. Run
`SYSTEM RESTORE REPLICA` on **one replica per shard first**, then restart the others so they
re-attach to the now-populated path.

```sql
-- On the first replica of each shard:
SYSTEM RESTORE REPLICA <db>.<table>;

-- On the remaining replicas of that shard:
SYSTEM RESTART REPLICA <db>.<table>;
```

`SYSTEM RESTORE REPLICA` rebuilds the keeper path from the data already on disk, so no data is
lost as long as the local parts are intact. Macros (`{shard}`, `{replica}`) and table DDL must
be unchanged so the recreated paths match the originals.

> Tip: generate the statements in bulk from `system.replicas` rather than typing each table.

---

## 4. Validate

```sql
-- No table should be read-only, and there should be no replication exceptions:
SELECT database, table, is_readonly, zookeeper_exception
FROM system.replicas WHERE is_readonly OR zookeeper_exception != '';

-- End-to-end replication check: insert on one replica, read from another.
INSERT INTO <db>.<table> (...) VALUES (...);     -- on replica A
SELECT count() FROM <db>.<table>;                 -- on replica B (after a moment)
```

- `system.replicas`: `is_readonly = 0`, `zookeeper_exception = ''` for all tables.
- A test row written on one replica appears on the other shard replica.
- `system.zookeeper` query against `/clickhouse` returns the rebuilt tree.

Only after this passes, **resume ingestion** (re-enable metadata-collector / ch-uploader).

---

## 5. Decommission the old keeper

Once replication is confirmed healthy on the new keeper, remove the manually-applied keeper so
nothing drifts back to it:

```sh
# In the monitoring namespace (old, kubectl-applied resources):
kubectl -n monitoring delete statefulset keeper
kubectl -n monitoring delete configmap keeper-config
kubectl -n monitoring delete pvc -l <old-keeper-selector>   # frees the 10Gi PVC
```

Confirm no remaining reference to `clickhouse-keeper.monitoring.svc:2181` exists in any CHI or
values file before deleting the PVC (the PVC is the only rollback anchor — see below).

---

## Rollback

The cutover is reversible **as long as the old keeper and its PVC still exist**, because the
old keeper retains the original replication metadata:

1. Revert the CHI values to the old keeper:
   `zookeeper.nodes: [{host: clickhouse-keeper.monitoring.svc.cluster.local, port: 2181}]`
   (or `keeper.enabled: false` if reverting to the pre-keeper state is intended).
2. `helmfile -e <env> -l name=clickhouse apply` and wait for the ClickHouse roll.
3. The tables reattach to the original keeper paths — no `RESTORE REPLICA` needed.

Do **not** delete the old keeper StatefulSet/ConfigMap/PVC (step 5) until the new keeper has
been validated and you are past the rollback window.
