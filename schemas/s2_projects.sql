-- s2_projects: FairShare project configuration snapshots.
-- ReplacingMergeTree keeps latest per project_id.
-- Polled every 600s.

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_projects ON CLUSTER '{cluster}'
(
    collected_at      DateTime64(3)  CODEC(Delta, ZSTD),
    project_id        String,
    project_name      String,
    fairshare_weight  Float32,
    gpu_limit         UInt32,
    metadata          String         -- JSON: full project config
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY project_id
SETTINGS index_granularity = 8192;
