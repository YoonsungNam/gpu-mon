-- s2_jobs: Time-series history of S2 batch scheduler jobs.
-- Each poll inserts a new row (running/pending jobs polled every 60s).
-- Enables: job wait time analysis, GPU-Hours calculation, per-job GPU Util JOIN.
-- TTL: 6 months.

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_jobs ON CLUSTER '{cluster}'
(
    collected_at  DateTime64(3)             CODEC(Delta, ZSTD),
    job_id        String,
    job_name      String,
    user_id       String,
    team          String,
    queue         LowCardinality(String),
    status        LowCardinality(String),   -- running, pending, completed, failed, cancelled
    submit_time   Nullable(DateTime64(3)),
    start_time    Nullable(DateTime64(3)),
    end_time      Nullable(DateTime64(3)),
    node_list     Array(String),            -- ["gpu-node-01", "gpu-node-03"]
    gpu_count     UInt16,
    gpu_indices   Array(UInt8),             -- [0, 1, 2, 3]
    cpu_count     UInt16,
    memory_mb     UInt32,
    exit_code     Nullable(Int32),
    metadata      String                   -- JSON: extra fields from S2 API
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(collected_at)
ORDER BY (status, collected_at, job_id)
TTL toDateTime(collected_at) + INTERVAL 180 DAY
SETTINGS index_granularity = 8192;
