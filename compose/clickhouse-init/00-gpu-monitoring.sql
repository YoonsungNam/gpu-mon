CREATE DATABASE IF NOT EXISTS gpu_monitoring;

CREATE TABLE IF NOT EXISTS gpu_monitoring.gpu_unified_logs
(
    timestamp   DateTime64(3)          CODEC(Delta, ZSTD),
    deployment_env LowCardinality(String),
    platform       LowCardinality(String),
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
ORDER BY (deployment_env, platform, cluster_id, node_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_jobs
(
    collected_at    DateTime64(3)        CODEC(Delta, ZSTD),
    grid_name       LowCardinality(String),
    job_id          UInt64,
    user            String,
    status          LowCardinality(String),
    project         LowCardinality(String),
    gpu_per_node    UInt16,
    num_nodes       UInt16,
    total_gpus      UInt32,
    submit_time     Nullable(DateTime64(3)),
    start_time      Nullable(DateTime64(3)),
    end_time        Nullable(DateTime64(3)),
    submit_ts       Float64,
    resources       Array(String),
    gpu_indices_raw String,
    gpu_allocations String,
    properties_json String,
    raw_json        String
)
ENGINE = ReplacingMergeTree(collected_at)
PARTITION BY toYYYYMM(collected_at)
ORDER BY (grid_name, job_id, submit_ts)
TTL toDateTime(collected_at) + INTERVAL 180 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS gpu_monitoring.s2_nodes
(
    collected_at  DateTime64(3)        CODEC(Delta, ZSTD),
    grid_name     LowCardinality(String),
    hostname      String,
    op_status     LowCardinality(String),
    link_status   LowCardinality(String),
    num_jobs      UInt16,
    cores_avail   UInt32,
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
