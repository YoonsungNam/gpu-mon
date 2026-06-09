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
  (client port `9181`), gated by the Helmfile state value `clickhouse_keeper.enabled`
  (which deploys the keeper release **and** injects the CHI's `keeper.enabled`), with the
  node list supplied via `keeper.nodes` in the environment's `clickhouse.yaml`.

> ⚠️ **This is a cutover, not an in-place upgrade.** The new keeper starts **empty**.
> ClickHouse stores its replication coordination metadata (replica registration, log
> pointers, part checksums) inside keeper. Pointing the CHI at an empty keeper makes all
> `Replicated*` tables **read-only** until that metadata is rebuilt. Schedule a maintenance
> window and stop writers first.

![Migration flow across the six phases: ClickHouse coordinates with the old keeper (:2181)
until cutover, then flips to the new keeper (:9181); the new keeper's metadata is rebuilt from
on-disk parts via SYSTEM RESTORE REPLICA, and the old keeper is kept untouched as the rollback
anchor until decommission.](img/keeper-migration-flow.png)

---

## 0. Prerequisites

- A maintenance window with **ingestion paused** (metadata-collector, ch-uploader, and any
  other writer to the ClickHouse cluster).
- `charts/clickhouse-keeper` chart merged (PR #66) and the corp/homelab `keeper.yaml`
  + `keeper.nodes` values ready (corp PR #24).
- **The enable gate.** The Helmfile state value `clickhouse_keeper.enabled` (default `false`
  in `environments/defaults.yaml`) is the single source of truth: it gates **both** the
  `clickhouse-keeper` release **and** the CHI's `zookeeper` block (the Helmfile injects
  `keeper.enabled` into the cluster release from it, overriding anything set in
  `clickhouse.yaml`). corp sets it in `environments/corp/values.yaml`; for an ad-hoc run pass
  `--state-values-set clickhouse_keeper.enabled=true` on every `helmfile` command below.
  > ⚠️ Once this flag is persisted `true`, **any** full `helmfile -e <env> apply` (CI,
  > GitOps, or another operator) will repoint ClickHouse at the new empty keeper and turn all
  > `Replicated*` tables read-only — outside your maintenance window. Between Section 1 and
  > Section 2, enable it ephemerally (`--state-values-set`) or run only targeted (`-l name=…`)
  > applies.
- For `replicasCount > 1` (e.g. corp), the CHI chart **fails the render** if keeper is enabled
  but `keeper.nodes` is empty, or if keeper is disabled. Always set `clickhouse_keeper.enabled`
  and `keeper.nodes` together. (homelab is `replicasCount: 1` and is unaffected.)
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

The `clickhouse-keeper` release is gated by `clickhouse_keeper.enabled` (default `false`), so
enable it for **this command only** — that deploys the keeper without yet arming the CHI's
zookeeper block. (If the gate is off, `helmfile` exits non-zero with `no releases found that
matches specified selector(name=clickhouse-keeper)`; enable it and re-run.)

```sh
helmfile -e <env> --state-values-set clickhouse_keeper.enabled=true -l name=clickhouse-keeper apply
```

> corp persists `clickhouse_keeper.enabled: true` in `values.yaml`, so it can drop the
> `--state-values-set` flag here — but it must then use only targeted `-l name=…` applies
> until the Section 2 cutover, to avoid an unrelated full apply repointing ClickHouse early.

Verify all three pods are Running and a leader is elected:

```sh
kubectl -n clickhouse get pod -l app.kubernetes.io/name=clickhouse-keeper

# Quorum health via 4-letter words. bash is present in the image (the entrypoint's /bin/sh is
# dash, which lacks /dev/tcp); ruok/mntr are in keeper's default whitelist, so no configmap
# change is needed. clickhouse-keeper-client speaks the znode protocol (not 4lw) and is not
# used here.
for i in 0 1 2; do
  echo "== keeper-$i =="
  kubectl -n clickhouse exec clickhouse-keeper-$i -- \
    bash -c 'exec 3<>/dev/tcp/127.0.0.1/9181; printf ruok >&3; timeout 2 cat <&3; echo'
done
# Expect "imok" from each.

# Roles: mntr reports zk_server_state — expect leader on exactly one node, follower on two.
for i in 0 1 2; do
  echo "== keeper-$i =="
  kubectl -n clickhouse exec clickhouse-keeper-$i -- \
    bash -c 'exec 3<>/dev/tcp/127.0.0.1/9181; printf mntr >&3; timeout 2 cat <&3' | grep zk_server_state
done
```

Do **not** proceed until exactly one `leader` and two `follower` nodes are reported.

> **Small clusters (1–2 nodes) — quorum is not node-failure HA.** The chart uses *soft*
> (preferred) anti-affinity, so on two nodes the three replicas pack as **2+1**. That schedules
> fine and survives *pod*-level disruption (a pod crash, a rolling restart, or a drain of the
> 1-pod node), but losing the node that holds **two** pods drops you to one and **breaks the
> raft quorum**. The chart ships a `PodDisruptionBudget` (`maxUnavailable: 1`, gpu-mon #73) so a
> `kubectl drain` / rolling node upgrade keeps a 2-of-3 majority — but a PDB only blocks
> *voluntary* evictions; it cannot protect against an *unplanned* loss of the 2-pod node. For
> true node-failure HA, run keeper on **≥3 schedulable nodes** and switch to hard anti-affinity
> (set `affinity` in `keeper.yaml`). With today's non-`Replicated` schemas this is low-stakes —
> a quorum loss only stalls `ON CLUSTER` DDL, it does not make existing tables read-only.

---

## 2. Cutover — repoint the CHI at the new keeper

Inside the maintenance window, with writers stopped:

1. Enable keeper coordination via the Helmfile state value, and supply the node list in
   `clickhouse.yaml`. Do **not** set `keeper.enabled` in `clickhouse.yaml` — the Helmfile
   injects it from `clickhouse_keeper.enabled` as the last values layer, so a value set there
   is ignored (corp PR #24 already configures both):

   ```yaml
   # environments/<env>/values.yaml  — the gate the Helmfile reads
   clickhouse_keeper:
     enabled: true        # deploys the keeper release AND arms the CHI zookeeper block
   ```

   ```yaml
   # environments/<env>/clickhouse.yaml  — only the node list belongs here
   keeper:
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

For every replicated table, recreate its keeper-side metadata from the local parts. On a
fresh/empty keeper **every replica's own znode** (`/clickhouse/tables/<path>/replicas/<name>`)
is gone, so `SYSTEM RESTORE REPLICA` must run on **every replica of every shard** — it is
**not** a one-per-shard operation. `SYSTEM RESTART REPLICA` alone on the other replicas does
**not** help here: it only re-reads *existing* keeper state, and an empty keeper has none, so
those replicas stay read-only.

```sql
-- Preferred at scale: fan the restore out to all replicas in one statement, per table.
SET distributed_ddl_task_timeout = 300;          -- many tables/replicas need a longer DDL timeout
SYSTEM RESTORE REPLICA <db>.<table> ON CLUSTER '<cluster>';
```

If you cannot use `ON CLUSTER`, run it on **each** replica individually:

```sql
-- Run on every replica that owns the table (every shard, every replica):
SYSTEM RESTORE REPLICA <db>.<table>;
```

`SYSTEM RESTORE REPLICA` rebuilds the keeper path from the data already on disk (parts are
re-registered, not re-fetched), so no data is lost as long as the local parts are intact. It
works only while the table is read-only (the state it is already in after the repoint). Macros
(`{shard}`, `{replica}`) and table DDL must be unchanged so the recreated paths match the
originals.

> If `RESTORE REPLICA` fails because a stale `/replicas/<name>` znode already exists (a re-run,
> or RESTORE already succeeded for that replica), clear the keeper-side entry first, then retry.
> This removes only metadata, not local data:
>
> ```sql
> -- Run from a DIFFERENT replica (DROP REPLICA cannot drop the local one), or use FROM ZKPATH:
> SYSTEM DROP REPLICA '<stale_replica_name>' FROM TABLE <db>.<table>;
> ```

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
- `system.zookeeper` shows the rebuilt tree. It **requires** an explicit `path` predicate
  (a bare `SELECT * FROM system.zookeeper` errors):

  ```sql
  -- Direct children of the keeper root; expect a 'tables' child after RESTORE:
  SELECT name, numChildren FROM system.zookeeper WHERE path = '/clickhouse' ORDER BY name;
  -- Confirm a given table's replicas all re-registered:
  SELECT name FROM system.zookeeper
  WHERE path = '/clickhouse/tables/<shard>/<db>/<table>/replicas' ORDER BY name;
  ```

Only after this passes, **resume ingestion** (re-enable metadata-collector / ch-uploader).

---

## 5. Decommission the old keeper

Once replication is confirmed healthy on the new keeper, remove the manually-applied keeper so
nothing drifts back to it.

The old keeper was applied by hand and is **not tracked in this repo**, so the names below are
illustrative. Discover the real objects first and substitute them:

```sh
# Identify the old (kubectl-applied) keeper resources in the monitoring namespace:
kubectl -n monitoring get sts,cm,svc,pvc | grep -i keeper

# Delete by the names you found (example names shown). Delete the PVC by name — a hand-applied
# StatefulSet PVC (vct `data` + STS `keeper` => `data-keeper-0`) may carry no labels, so a
# `-l` selector can silently match nothing.
kubectl -n monitoring delete statefulset keeper
kubectl -n monitoring delete configmap keeper-config
kubectl -n monitoring delete pvc data-keeper-0      # frees the 10Gi PVC
```

Confirm no remaining reference to `clickhouse-keeper.monitoring.svc:2181` exists in any CHI or
values file before deleting the PVC (the PVC is the only rollback anchor — see below).

---

## Rollback

The cutover is reversible **as long as the old keeper and its PVC still exist**, because the
old keeper retains the original replication metadata:

1. Point ClickHouse back at the old keeper by editing `keeper.nodes` (the chart renders the
   CHI's `zookeeper.nodes` from this — there is no `zookeeper.nodes` chart input), keeping the
   keeper enabled via `clickhouse_keeper.enabled=true`:

   ```yaml
   # environments/<env>/clickhouse.yaml
   keeper:
     nodes:
       - host: clickhouse-keeper.monitoring.svc.cluster.local
         port: 2181
   ```

   For a `replicasCount > 1` cluster, do **not** set `clickhouse_keeper.enabled=false` to roll
   back — the CHI preflight fails the render when replicas need coordination but keeper is off.
   Disabling keeper is only valid when reverting the whole cluster to single-replica.
2. `helmfile -e <env> -l name=clickhouse apply` and wait for the ClickHouse roll.
3. The tables reattach to the original keeper paths — no `RESTORE REPLICA` needed.

Do **not** delete the old keeper StatefulSet/ConfigMap/PVC (step 5) until the new keeper has
been validated and you are past the rollback window.
