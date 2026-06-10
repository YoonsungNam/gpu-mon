-- s2_jobs: S2 batch scheduler jobs (BMI + phd source).
-- ReplacingMergeTree keeps the latest-collected row (latest status) per
-- (grid_name, job_id, submit_ts). Reads use FINAL. Completed jobs are collected
-- incrementally by end_time; the overlap window's duplicates collapse here.
-- TTL: 6 months.

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_jobs ON CLUSTER '{cluster}'
(
    collected_at    DateTime64(3)            CODEC(Delta, ZSTD),
    grid_name       LowCardinality(String),
    job_id          UInt64,
    user            String,
    status          LowCardinality(String),   -- Running, Queued, Done, Failed, Stopped
    project         LowCardinality(String),
    gpu_per_node    UInt16,                   -- from phd @cores@ (S2 accounts 1 GPU card = 1 core)
    num_nodes       UInt16,
    total_gpus      UInt32,                   -- gpu_per_node * num_nodes
    submit_time     Nullable(DateTime64(3)),
    start_time      Nullable(DateTime64(3)),
    end_time        Nullable(DateTime64(3)),
    submit_ts       Float64,                  -- submit time as Unix epoch (part of the job key)
    resources       Array(String),
    gpu_indices_raw String,                   -- raw _gpu_indices property
    gpu_allocations String,                   -- JSON: [{host, gpu_ids}]
    properties_json String,                   -- JSON: full phd properties map
    raw_json        String                    -- JSON: raw parsed job
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(collected_at)
ORDER BY (grid_name, job_id, submit_ts)
TTL toDateTime(collected_at) + INTERVAL 180 DAY
SETTINGS index_granularity = 8192;
