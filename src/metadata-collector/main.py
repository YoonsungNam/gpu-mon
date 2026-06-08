"""
Metadata Collector

Polls batch-scheduler / hypervisor APIs (S2 batch scheduler, VMware vCenter)
on configurable schedules and batch-inserts metadata into ClickHouse.

Config: /etc/metadata-collector/config.yaml  or  MC_CONFIG_PATH env var
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
from pathlib import Path

logger = logging.getLogger(__name__)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    base_dir = Path(__file__).resolve().parent
    config_path = os.environ.get(
        "MC_CONFIG_PATH",
        str(base_dir / "config.yaml")
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
        ssh_cfg = s2_cfg.get("ssh", {})
        s2 = S2Adapter(
            bmi_url=s2_cfg.get("bmi_url", ""),
            phd_bin_template=s2_cfg.get(
                "phd_bin_template",
                "/opt/s2/installed/{grid_name}/current/bin/phd",
            ),
            http_proxy_addr=s2_cfg.get("http_proxy_addr", ""),
            ssh_jump_host=ssh_cfg.get("jump_host", ""),
            ssh_login_host=ssh_cfg.get("login_host", ""),
            writer=writer,
            ssh_user=ssh_cfg.get("user"),
            ssh_key_path=ssh_cfg.get("key_path"),
            target_grids=s2_cfg.get("target_grids"),
        )

        intervals = s2_cfg.get("intervals", {})

        # grid discovery — every 60 min
        scheduler.add(
            s2.refresh_grids,
            interval_secs=intervals.get("grid_discovery", 3600),
            name="s2-grid-discovery",
        )
        # node info — every 10 min
        scheduler.add(
            s2.collect_nodes,
            interval_secs=intervals.get("nodes", 600),
            name="s2-nodes",
        )
        # Running + Queued jobs — every 5 min
        scheduler.add(
            s2.collect_jobs_active,
            interval_secs=intervals.get("jobs_active", 300),
            name="s2-jobs-active",
        )
        # Done + Failed + Stopped jobs — every 10 min (incremental)
        scheduler.add(
            s2.collect_jobs_completed,
            interval_secs=intervals.get("jobs_completed", 600),
            name="s2-jobs-completed",
        )

        logger.info(
            "S2 adapter enabled: bmi=%s, login=%s",
            s2_cfg.get("bmi_url"), ssh_cfg.get("login_host"),
        )

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
