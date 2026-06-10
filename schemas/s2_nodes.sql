-- s2_nodes: Current state of S2 scheduler computing nodes (phd host source).
-- ReplacingMergeTree keeps only the latest row per (grid_name, hostname).
-- Polled every 10 min.

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_nodes ON CLUSTER '{cluster}'
(
    collected_at  DateTime64(3)           CODEC(Delta, ZSTD),
    grid_name     LowCardinality(String),
    hostname      String,
    op_status     LowCardinality(String),
    link_status   LowCardinality(String),
    num_jobs      UInt16,
    cores_avail   UInt32,                 -- S2 core accounting: on GPU grids 1 GPU card = 1 core
    cores_used    UInt32,
    mem_avail_gb  Float32,
    mem_total_gb  Float32,
    swap_avail_gb Float32,
    swap_total_gb Float32,
    load_1m       Float32,
    load_5m       Float32,
    load_15m      Float32,
    controller    LowCardinality(String),
    raw_json      String
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY (grid_name, hostname)
SETTINGS index_granularity = 8192;
