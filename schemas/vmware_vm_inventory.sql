-- vmware_vm_inventory: Current GPU VM inventory from VMware vCenter.
-- ReplacingMergeTree keeps latest per vm_uuid.
-- Polled every 300s. TTL: 12 months.

CREATE TABLE IF NOT EXISTS gpu_monitoring.vmware_vm_inventory ON CLUSTER '{cluster}'
(
    collected_at  DateTime64(3)           CODEC(Delta, ZSTD),
    vm_name       String,
    vm_uuid       String,
    vm_status     LowCardinality(String), -- poweredOn, poweredOff, suspended
    esxi_host     String,
    cluster       String,
    resource_pool String,
    guest_os      String,
    vcpu_count    UInt16,
    memory_mb     UInt32,
    gpu_count     UInt8,
    gpu_type      LowCardinality(String), -- passthrough, vgpu
    gpu_profile   String,
    gpu_pci_ids   String,                 -- JSON array of PCI device IDs
    annotation    String,
    metadata      String                  -- JSON: datacenter, folder, etc.
)
ENGINE = ReplacingMergeTree(collected_at)
ORDER BY vm_uuid
TTL toDateTime(collected_at) + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;
