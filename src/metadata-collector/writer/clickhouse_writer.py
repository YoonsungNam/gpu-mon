"""
ClickHouse batch writer.

Buffers rows per table and flushes them as batch INSERTs.
Thread-safe; called from multiple scheduler threads.
"""

import logging
import threading
from typing import List, Dict

logger = logging.getLogger(__name__)

try:
    import clickhouse_driver
    DRIVER_AVAILABLE = True
except ImportError:
    DRIVER_AVAILABLE = False
    logger.warning("clickhouse-driver not installed; writes will be no-ops.")


class ClickHouseWriter:
    def __init__(
        self,
        endpoints: List[str],
        database: str,
        username: str,
        password: str,
        batch_size: int = 500,
        flush_interval: str = "10s",
    ):
        if not DRIVER_AVAILABLE:
            raise RuntimeError(
                "clickhouse-driver is required. Install: pip install clickhouse-driver"
            )
        host, _, port_str = endpoints[0].partition(":")
        port = int(port_str) if port_str else 9000

        self._client = clickhouse_driver.Client(
            host=host,
            port=port,
            database=database,
            user=username,
            password=password,
        )
        self._batch_size = batch_size
        self._buffers: Dict[str, List[Dict]] = {}
        self._lock = threading.Lock()

    def insert(self, table: str, rows: List[Dict]):
        with self._lock:
            buf = self._buffers.setdefault(table, [])
            buf.extend(rows)
            if len(buf) >= self._batch_size:
                self._flush_table(table)

    def flush(self):
        with self._lock:
            for table in list(self._buffers.keys()):
                self._flush_table(table)

    def _flush_table(self, table: str):
        """Must be called with self._lock held."""
        rows = self._buffers.pop(table, [])
        if not rows:
            return
        try:
            self._client.execute(f"INSERT INTO {table} VALUES", rows)
            logger.debug("Flushed %d rows to %s", len(rows), table)
        except Exception as e:
            logger.error("Failed to flush %d rows to %s: %s", len(rows), table, e)
            # Re-buffer on failure to avoid data loss
            self._buffers.setdefault(table, []).extend(rows)
