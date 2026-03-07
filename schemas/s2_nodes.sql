-- s2_nodes: Current state of S2 scheduler nodes.
-- ReplacingMergeTree keeps only the latest row per node_id.
-- Polled every 120s.

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_nodes ON CLUSTER '{cluster}'
(
    collected_at  DateTime64(3)           CODEC(Delta, ZSTD),
    node_id       String,
    status        LowCardinality(String), -- idle, alloc, drain, down
    partition     LowCardinality(String),
    gpu_total     UInt16,
    gpu_allocated UInt16,
    cpu_total     UInt16,
    cpu_allocated UInt16,
    metadata      String
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY node_id
SETTINGS index_granularity = 8192;
