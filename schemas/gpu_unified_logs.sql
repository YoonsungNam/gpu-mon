-- gpu_unified_logs: Normalized log table for all environments.
-- Receives data from Vector Aggregator (push from node agents).
-- TTL: configurable via environment values.

CREATE DATABASE IF NOT EXISTS gpu_monitoring ON CLUSTER '{cluster}';

CREATE TABLE IF NOT EXISTS gpu_monitoring.gpu_unified_logs ON CLUSTER '{cluster}'
(
    timestamp   DateTime64(3)                    CODEC(Delta, ZSTD),
    env         LowCardinality(String),           -- baremetal, k8s, vm, homelab
    cluster_id  LowCardinality(String),
    node_id     String,
    gpu_id      Nullable(UInt8),
    log_level   LowCardinality(String),           -- INFO, WARN, ERROR, FATAL
    source      LowCardinality(String),           -- driver, scheduler, kubelet, system, nccl
    message     String,
    metadata    String                            -- JSON: pid, container, namespace, etc.
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (env, cluster_id, node_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;
