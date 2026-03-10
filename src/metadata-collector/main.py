"""
Metadata Collector

Polls legacy system APIs (Samsung S2 batch scheduler, VMware vCenter)
on configurable schedules and batch-inserts metadata into ClickHouse.

Enables GPU metric enrichment: GPU Util + Job context at query time.

Config: /etc/metadata-collector/config.yaml
        or MC_CONFIG_PATH env var
"""

import logging
import os
import signal
import sys

import yaml
from adapters.s2_adapter import S2Adapter
from adapters.vmware_adapter import VMwareAdapter
from health import HealthServer
from scheduler import CollectorScheduler
from writer.clickhouse_writer import ClickHouseWriter

logger = logging.getLogger(__name__)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    config_path = os.environ.get(
        "MC_CONFIG_PATH", "/etc/metadata-collector/config.yaml"
    )

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    log_level = config.get("collector", {}).get("log_level", "info").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    ch_cfg = config["clickhouse"]
    writer = ClickHouseWriter(
        endpoints=ch_cfg["endpoints"],
        database=ch_cfg["database"],
        username=ch_cfg["username"],
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        batch_size=ch_cfg.get("batch_size", 500),
        flush_interval=ch_cfg.get("flush_interval", "10s"),
    )

    scheduler = CollectorScheduler()

    sources = config.get("sources", {})

    # ── S2 Adapter ───────────────────────────────────────────────────────
    s2_cfg = sources.get("s2", {})
    if s2_cfg.get("enabled", False):
        s2 = S2Adapter(
            api_url=s2_cfg.get("api_url", ""),
            api_token=os.environ.get("S2_API_TOKEN", ""),
            writer=writer,
        )
        scheduler.add(s2.collect_jobs_running,    interval_secs=60,  name="s2-jobs-running")
        scheduler.add(s2.collect_jobs_completed,  interval_secs=300, name="s2-jobs-completed")
        scheduler.add(s2.collect_nodes,           interval_secs=120, name="s2-nodes")
        scheduler.add(s2.collect_projects,        interval_secs=600, name="s2-projects")
        scheduler.add(s2.collect_pools,           interval_secs=600, name="s2-pools")
        logger.info("S2 adapter enabled: %s", s2_cfg.get("api_url"))

    # ── VMware Adapter ───────────────────────────────────────────────────
    vmw_cfg = sources.get("vmware", {})
    if vmw_cfg.get("enabled", False):
        vmw = VMwareAdapter(
            vcenter_url=vmw_cfg.get("vcenter_url", ""),
            username=os.environ.get("VCENTER_USERNAME", ""),
            password=os.environ.get("VCENTER_PASSWORD", ""),
            insecure=vmw_cfg.get("insecure_skip_verify", False),
            writer=writer,
        )
        scheduler.add(vmw.collect_vm_inventory, interval_secs=300, name="vmware-inventory")
        logger.info("VMware adapter enabled: %s", vmw_cfg.get("vcenter_url"))

    if not (s2_cfg.get("enabled") or vmw_cfg.get("enabled")):
        logger.warning("No sources enabled. Check config.yaml.")

    # ── Health server ────────────────────────────────────────────────────
    health_port = config.get("collector", {}).get("health_port", 8080)
    health = HealthServer(port=health_port)
    health.start()

    # ── Graceful shutdown ────────────────────────────────────────────────
    def _shutdown(signum, frame):
        logger.info("Shutting down metadata-collector…")
        scheduler.stop()
        writer.flush()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    scheduler.run_forever()


if __name__ == "__main__":
    main()
