# macbook Quickstart

Get the full monitoring stack running locally in minutes, without any GPU hardware.

## Prerequisites

- Docker Desktop (running)
- `make`

## Start

```bash
git clone https://github.com/YoonsungNam/gpu-mon.git
cd gpu-mon

./scripts/setup-macbook.sh   # one-time check
make dev-up
```

Open [http://localhost:3000](http://localhost:3000) — Grafana (admin / admin).

## What runs

| Container | Port | Description |
|---|---|---|
| mock-dcgm-exporter | 9400 | Synthetic GPU metrics (4 nodes × 2 GPUs) |
| vmagent | 8429 | Scrapes mock exporter, writes to VictoriaMetrics |
| vminsert | 8480 | VictoriaMetrics insert endpoint |
| vmselect | 8481 | VictoriaMetrics query endpoint |
| vmstorage | — | VictoriaMetrics storage (internal) |
| clickhouse | 8123, 9000 | Log and metadata storage |
| vector | 6000 | Log aggregator (accepts Vector protocol) |
| grafana | 3000 | Dashboards |

## Stop

```bash
make dev-down
```

## Useful commands

```bash
make dev-logs        # tail all container logs
make dev-ps          # show running containers

# Query metrics directly
curl -s "http://localhost:8481/select/0/prometheus/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL" | jq .
```
