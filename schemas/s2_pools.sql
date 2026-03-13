-- s2_pools: Logical node pool configuration snapshots.
-- ReplacingMergeTree keeps latest per pool_id.
-- Polled every 600s.

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_pools ON CLUSTER '{cluster}'
(
    collected_at DateTime64(3)  CODEC(Delta, ZSTD),
    pool_id      String,
    pool_name    String,
    node_list    Array(String),
    gpu_total    UInt32,
    metadata     String          -- JSON: scheduling policy, constraints
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY pool_id
SETTINGS index_granularity = 8192;
