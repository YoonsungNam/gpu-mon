# Grafana Dashboards

Grafana dashboard JSON files for gpu-mon.

| File | Description |
|---|---|
| `gpu-overview.json` | Cluster-wide GPU utilization, temperature, power |
| `gpu-efficiency.json` | L2 efficiency: Tensor Core, SM Occupancy, DRAM BW |
| `inference-servers.json` | vLLM/TGI/Triton: TTFT, TPOT, KV Cache, throughput |
| `job-explorer.json` | S2 Job metadata + GPU Util join |
| `vm-inventory.json` | VMware GPU VM inventory and ESXi host mapping |
| `node-health.json` | Node-level system metrics (CPU, Memory, Disk, Network) |

Dashboards are automatically provisioned from this directory via Grafana provisioning.
