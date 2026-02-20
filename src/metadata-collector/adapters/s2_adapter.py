"""
Samsung S2 Batch Scheduler adapter.

Polls S2 REST API and normalizes job/node/project/pool data
into the gpu_monitoring ClickHouse schema.
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

import requests

from .base import MetadataAdapter

logger = logging.getLogger(__name__)


def _parse_time(val) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None


class S2Adapter:
    """Wraps multiple S2 data types behind a single API client."""

    def __init__(self, api_url: str, api_token: str, writer):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_token}"
        self.session.headers["Accept"] = "application/json"
        self.writer = writer

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.api_url}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── Jobs ─────────────────────────────────────────────────────────────

    def collect_jobs_running(self):
        self._collect_jobs(statuses=["running", "pending"])

    def collect_jobs_completed(self):
        self._collect_jobs(statuses=["completed", "failed", "cancelled"], since="24h")

    def _collect_jobs(self, statuses: List[str], since: str = None):
        try:
            params = {"status": ",".join(statuses)}
            if since:
                params["since"] = since
            data = self._get("/jobs", params)
            rows = [_normalize_job(j) for j in data.get("jobs", [])]
            if rows:
                self.writer.insert("s2_jobs", rows)
            logger.debug("s2 jobs collected: %d rows (%s)", len(rows), statuses)
        except Exception as e:
            logger.error("s2 collect_jobs failed: %s", e)

    # ── Nodes ─────────────────────────────────────────────────────────────

    def collect_nodes(self):
        try:
            data = self._get("/nodes")
            rows = [_normalize_node(n) for n in data.get("nodes", [])]
            if rows:
                self.writer.insert("s2_nodes", rows)
            logger.debug("s2 nodes collected: %d rows", len(rows))
        except Exception as e:
            logger.error("s2 collect_nodes failed: %s", e)

    # ── Projects ──────────────────────────────────────────────────────────

    def collect_projects(self):
        try:
            data = self._get("/projects")
            rows = [_normalize_project(p) for p in data.get("projects", [])]
            if rows:
                self.writer.insert("s2_projects", rows)
            logger.debug("s2 projects collected: %d rows", len(rows))
        except Exception as e:
            logger.error("s2 collect_projects failed: %s", e)

    # ── Pools ─────────────────────────────────────────────────────────────

    def collect_pools(self):
        try:
            data = self._get("/pools")
            rows = [_normalize_pool(p) for p in data.get("pools", [])]
            if rows:
                self.writer.insert("s2_pools", rows)
            logger.debug("s2 pools collected: %d rows", len(rows))
        except Exception as e:
            logger.error("s2 collect_pools failed: %s", e)


# ─── Normalizers ──────────────────────────────────────────────────────────────

def _normalize_job(raw: Dict) -> Dict:
    return {
        "collected_at":  datetime.now(timezone.utc),
        "job_id":        str(raw.get("id", "")),
        "job_name":      raw.get("name", ""),
        "user_id":       raw.get("user", ""),
        "team":          raw.get("group", ""),
        "queue":         raw.get("partition", "default"),
        "status":        raw.get("state", ""),
        "submit_time":   _parse_time(raw.get("submit_time")),
        "start_time":    _parse_time(raw.get("start_time")),
        "end_time":      _parse_time(raw.get("end_time")),
        "node_list":     raw.get("nodes", []),
        "gpu_count":     int(raw.get("gpu_count", 0)),
        "gpu_indices":   raw.get("gpu_indices", []),
        "cpu_count":     int(raw.get("cpu_count", 0)),
        "memory_mb":     int(raw.get("memory_mb", 0)),
        "exit_code":     raw.get("exit_code"),
        "metadata":      json.dumps(raw.get("extra", {})),
    }


def _normalize_node(raw: Dict) -> Dict:
    return {
        "collected_at":  datetime.now(timezone.utc),
        "node_id":       raw.get("name", ""),
        "status":        raw.get("state", ""),
        "partition":     raw.get("partition", ""),
        "gpu_total":     int(raw.get("gpu_total", 0)),
        "gpu_allocated": int(raw.get("gpu_allocated", 0)),
        "cpu_total":     int(raw.get("cpu_total", 0)),
        "cpu_allocated": int(raw.get("cpu_allocated", 0)),
        "metadata":      json.dumps(raw.get("extra", {})),
    }


def _normalize_project(raw: Dict) -> Dict:
    return {
        "collected_at":     datetime.now(timezone.utc),
        "project_id":       str(raw.get("id", "")),
        "project_name":     raw.get("name", ""),
        "fairshare_weight": float(raw.get("fairshare", 1.0)),
        "gpu_limit":        int(raw.get("gpu_limit", 0)),
        "metadata":         json.dumps(raw),
    }


def _normalize_pool(raw: Dict) -> Dict:
    return {
        "collected_at": datetime.now(timezone.utc),
        "pool_id":      str(raw.get("id", "")),
        "pool_name":    raw.get("name", ""),
        "node_list":    raw.get("nodes", []),
        "gpu_total":    int(raw.get("gpu_total", 0)),
        "metadata":     json.dumps(raw),
    }
