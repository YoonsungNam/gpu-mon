CREATE DATABASE IF NOT EXISTS gpu_monitoring;

CREATE TABLE IF NOT EXISTS gpu_monitoring.gpu_unified_logs
(
    timestamp   DateTime64(3)          CODEC(Delta, ZSTD),
    env         LowCardinality(String),
    cluster_id  LowCardinality(String),
    node_id     String,
    gpu_id      Nullable(UInt8),
    log_level   LowCardinality(String),
    source      LowCardinality(String),
    message     String,
    metadata    String
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (env, cluster_id, node_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_jobs
(
    collected_at  DateTime64(3)        CODEC(Delta, ZSTD),
    job_id        String,
    job_name      String,
    user_id       String,
    team          String,
    queue         LowCardinality(String),
    status        LowCardinality(String),
    submit_time   Nullable(DateTime64(3)),
    start_time    Nullable(DateTime64(3)),
    end_time      Nullable(DateTime64(3)),
    node_list     Array(String),
    gpu_count     UInt16,
    gpu_indices   Array(UInt8),
    cpu_count     UInt16,
    memory_mb     UInt32,
    exit_code     Nullable(Int32),
    metadata      String
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(collected_at)
ORDER BY (status, collected_at, job_id)
TTL toDateTime(collected_at) + INTERVAL 180 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_nodes
(
    collected_at  DateTime64(3)        CODEC(Delta, ZSTD),
    node_id       String,
    status        LowCardinality(String),
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

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_pools
(
    collected_at DateTime64(3)         CODEC(Delta, ZSTD),
    pool_id      String,
    pool_name    String,
    node_list    Array(String),
    gpu_total    UInt32,
    metadata     String
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY pool_id
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_projects
(
    collected_at      DateTime64(3)    CODEC(Delta, ZSTD),
    project_id        String,
    project_name      String,
    fairshare_weight  Float32,
    gpu_limit         UInt32,
    metadata          String
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY project_id
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS gpu_monitoring.vmware_vm_inventory
(
    collected_at  DateTime64(3)           CODEC(Delta, ZSTD),
    vm_name       String,
    vm_uuid       String,
    vm_status     LowCardinality(String),
    esxi_host     String,
    cluster       String,
    resource_pool String,
    guest_os      String,
    vcpu_count    UInt16,
    memory_mb     UInt32,
    gpu_count     UInt8,
    gpu_type      LowCardinality(String),
    gpu_profile   String,
    gpu_pci_ids   String,
    annotation    String,
    metadata      String
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY vm_uuid
TTL toDateTime(collected_at) + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;
