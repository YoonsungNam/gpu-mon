"""
S2 Batch Scheduler adapter.

Collects GPU node/job metadata from the in-house S2 scheduler via:
  1) BMI REST API   → grid discovery & portal node spec  (via Squid proxy)
  2) ssh -J         → run ``phd host`` on the login server   (via jump host)
  3) ssh -J         → run ``phd list`` on the login server   (via jump host)

Job collection strategy
-----------------------
- **Active jobs** (Running + Queued): full collection every cycle.
    ``phd list -a -r``  /  ``phd list -a -q``
- **Completed jobs** (Done + Failed + Stopped): end_time-based window collection.
    ``phd list -a -sel "(status==Done || ...) && end_time > TS"``
  TS = last collected end_time - overlap buffer (10 min).
  Duplicates caused by the overlap are removed by ReplacingMergeTree.
  On pod restart, the last end_time is recovered from ClickHouse.

ClickHouse target tables:
  - s2_nodes  (ReplacingMergeTree)
  - s2_jobs   (ReplacingMergeTree)
"""

import ast
import json
import logging
import re
import shlex
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# KST (UTC+9)
_KST = timezone(timedelta(hours=9))

# overlap buffer (seconds) for completed job collection.
# Prevents missing jobs at the previous collection boundary. Duplicates are
# handled by ReplacingMergeTree.
_COMPLETED_OVERLAP_SECS = 600  # 10 min

# Maximum lookback range (seconds) on the first collection (last_end_ts=0).
# Avoids a full scan to prevent OOM.
_INITIAL_LOOKBACK_SECS = 86400 * 7  # 7 days


# ═══════════════════════════════════════════════════════════════════════
#  Utility helpers
# ═══════════════════════════════════════════════════════════════════════

def _int(s: str) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _ts_to_datetime(ts: float) -> Optional[datetime]:
    """Unix timestamp → KST datetime.  Returns None if 0."""
    if not ts or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=_KST)
    except (OSError, ValueError):
        return None


def _safe_literal_eval(s: str):
    try:
        return ast.literal_eval(s.strip())
    except (ValueError, SyntaxError):
        return None


def _parse_gpu_indices(gpu_indices_str: str) -> List[Dict[str, Any]]:
    """_gpu_indices string → per-host GPU allocation list."""
    if not gpu_indices_str:
        return []
    allocations: List[Dict[str, Any]] = []
    for segment in gpu_indices_str.split("|"):
        segment = segment.strip()
        if not segment:
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+)\[([^\]]+)\]$", segment)
        if not m:
            logger.warning("gpu_indices segment parse fail: %s", segment)
            continue
        host = m.group(1)
        gpu_ids = []
        for part in m.group(2).split(","):
            nums = re.findall(r"\d+", part.strip())
            if nums:
                gpu_ids.append(int(nums[-1]))
        allocations.append({"host": host, "gpu_ids": gpu_ids})
    return allocations


# ═══════════════════════════════════════════════════════════════════════
#  BMI Client
# ═══════════════════════════════════════════════════════════════════════

class _BMIClient:
    def __init__(self, base_url: str, proxies: dict):
        self._base = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.proxies.update(proxies)
        self._session.headers["Accept"] = "application/json"

    def get_grids(self) -> List[str]:
        url = f"{self._base}/api/v2/grid"
        logger.info("BMI GET %s", url)
        resp = self._session.get(url, timeout=15)
        resp.raise_for_status()
        return [item["name"] for item in resp.json()]

    def get_portal_spec(self, grid_name: str) -> str:
        url = f"{self._base}/api/v2/grid/{grid_name}/machinelist/pnodes"
        logger.info("BMI GET %s", url)
        resp = self._session.get(url, timeout=15)
        resp.raise_for_status()
        pnodes = resp.json()
        if not pnodes:
            return ""
        pn = pnodes[0]
        machine = pn["machine"].split(".")[0]
        return f"{pn['port']}@{machine}"


# ═══════════════════════════════════════════════════════════════════════
#  SSH PHD runner
# ═══════════════════════════════════════════════════════════════════════

class PhdCommandError(RuntimeError):
    """The remote phd command could not be executed or exited non-zero."""


class _SSHPHDRunner:
    def __init__(
        self,
        phd_bin_template: str,
        jump_host: str,
        login_host: str,
        ssh_user: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        ssh_port: int = 22,
        ssh_options: Optional[List[str]] = None,
    ):
        self._phd_template = phd_bin_template
        self._jump_host = jump_host
        self._login_host = login_host
        self._ssh_user = ssh_user
        self._ssh_key_path = ssh_key_path
        self._ssh_port = ssh_port
        self._ssh_options = ssh_options or []

    def _build_ssh_cmd(self, remote_cmd: str) -> List[str]:
        ssh_opts = (
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null "
            "-o BatchMode=yes"
        )
        jump_spec = self._jump_host
        if self._ssh_user:
            jump_spec = f"{self._ssh_user}@{self._jump_host}"
        key_opt = f"-i {self._ssh_key_path} " if self._ssh_key_path else ""
        proxy_cmd = f"ssh {ssh_opts} {key_opt}-W %h:%p {jump_spec}"

        cmd = ["ssh"]
        cmd += [
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=15",
            "-o", f"ProxyCommand={proxy_cmd}",
        ]
        if self._ssh_key_path:
            cmd += ["-i", self._ssh_key_path]
        if self._ssh_port != 22:
            cmd += ["-p", str(self._ssh_port)]
        cmd += self._ssh_options

        target = self._login_host
        if self._ssh_user:
            target = f"{self._ssh_user}@{self._login_host}"
        cmd.append(target)
        cmd.append(remote_cmd)
        return cmd

    def run(self, args: List[str], grid_name: str) -> str:
        """Run phd remotely and return its stdout.

        Raises PhdCommandError on any execution failure (non-zero exit,
        timeout, missing ssh binary) so callers can tell "phd failed" apart
        from "phd succeeded with no matching jobs" (empty stdout). Returning
        an empty string on failure would make vanished-job detection treat
        every active job as gone.
        """
        phd_bin = self._phd_template.format(grid_name=grid_name)
        remote_parts = [phd_bin] + args
        remote_cmd = " ".join(shlex.quote(p) for p in remote_parts)
        ssh_cmd = self._build_ssh_cmd(remote_cmd)
        logger.debug("exec: %s", " ".join(ssh_cmd))

        try:
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            logger.error("ssh+phd timeout (host=%s)", self._login_host)
            raise PhdCommandError(
                f"ssh+phd timeout (host={self._login_host})"
            ) from e
        except FileNotFoundError as e:
            logger.error("ssh binary not found in PATH")
            raise PhdCommandError("ssh binary not found in PATH") from e

        if result.returncode != 0:
            logger.error(
                "ssh+phd failed (rc=%d, host=%s): %s",
                result.returncode, self._login_host, result.stderr.strip(),
            )
            raise PhdCommandError(
                f"ssh+phd failed (rc={result.returncode}, "
                f"host={self._login_host}): {result.stderr.strip()}"
            )
        return result.stdout


# ═══════════════════════════════════════════════════════════════════════
#  Parsers
# ═══════════════════════════════════════════════════════════════════════

def _parse_phd_host(raw: str) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    in_data = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("---"):
            in_data = True
            continue
        if not in_data or not stripped:
            continue
        tokens = stripped.split()
        if len(tokens) < 13:
            logger.warning("node line skipped (tokens=%d): %s", len(tokens), stripped)
            continue
        op_link = tokens[1].split("/", 1)
        nodes.append({
            "hostname":      tokens[0],
            "op_status":     op_link[0],
            "link_status":   op_link[1] if len(op_link) > 1 else "",
            "num_jobs":      _int(tokens[2]),
            "cores_avail":   _int(tokens[3]),
            "cores_used":    _int(tokens[4]),
            "mem_avail_gb":  _float(tokens[5]),
            "mem_total_gb":  _float(tokens[6]),
            "swap_avail_gb": _float(tokens[7]),
            "swap_total_gb": _float(tokens[8]),
            "load_1m":       _float(tokens[9]),
            "load_5m":       _float(tokens[10]),
            "load_15m":      _float(tokens[11]),
            "controller":    tokens[12],
        })
    return nodes


# Note @cores@: S2 treats GPUs as first-class assets and accounts one GPU
# card as one core, so on GPU grids @cores@ is the per-node GPU count (the
# token name is historical, from CPU scheduling). Confirmed by the S2
# operators; this is why it maps to gpu_per_node below.
_LIST_FORMAT = (
    "@id@ @user@ @status@ @project@ @cores@ @dp_num_cnodes@ "
    "@submit_time@ @start_time@ @end_time@ "
    "@resources@ @properties@"
)


def _parse_phd_list(raw: str) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        job = _parse_job_line(stripped)
        if job is not None:
            jobs.append(job)
    return jobs


def _parse_job_line(line: str) -> Optional[Dict[str, Any]]:
    """9 fixed fields + [...] resources + {...} properties."""
    try:
        bracket_idx = line.index("[")
    except ValueError:
        logger.warning("job line has no array: %s", line)
        return None

    fixed = line[:bracket_idx].strip().split()
    if len(fixed) < 9:
        logger.warning("job line fixed fields < 9: %s", line)
        return None

    rest = line[bracket_idx:]

    resources = []
    res_match = re.search(r"\[.*?\]", rest)
    if res_match:
        parsed = _safe_literal_eval(res_match.group())
        if isinstance(parsed, list):
            resources = [str(x) for x in parsed]

    properties = {}
    props_match = re.search(r"\{.*\}", rest)
    if props_match:
        parsed = _safe_literal_eval(props_match.group())
        if isinstance(parsed, dict):
            properties = parsed

    gpu_per_node = _int(fixed[4])
    num_nodes = _int(fixed[5])
    submit_ts = _float(fixed[6])
    start_ts = _float(fixed[7])
    end_ts = _float(fixed[8])

    gpu_indices_raw = str(properties.get("_gpu_indices", ""))
    gpu_allocations = _parse_gpu_indices(gpu_indices_raw)

    return {
        "job_id":           int(fixed[0]),
        "user":             fixed[1],
        "status":           fixed[2],
        "project":          fixed[3],
        "gpu_per_node":     gpu_per_node,
        "num_nodes":        num_nodes,
        "total_gpus":       gpu_per_node * num_nodes,
        "submit_time":      _ts_to_datetime(submit_ts),
        "start_time":       _ts_to_datetime(start_ts),
        "end_time":         _ts_to_datetime(end_ts),
        "submit_ts":        submit_ts,
        "end_ts":           end_ts,
        "resources":        resources,
        "gpu_indices_raw":  gpu_indices_raw,
        "gpu_allocations":  gpu_allocations,
        "properties":       properties,
    }


# ═══════════════════════════════════════════════════════════════════════
#  Normalizers
# ═══════════════════════════════════════════════════════════════════════

def _normalize_node(grid_name: str, raw: Dict) -> Dict:
    return {
        "collected_at":  datetime.now(_KST),
        "grid_name":     grid_name,
        "hostname":      raw["hostname"],
        "op_status":     raw["op_status"],
        "link_status":   raw["link_status"],
        "num_jobs":      raw["num_jobs"],
        "cores_avail":   raw["cores_avail"],
        "cores_used":    raw["cores_used"],
        "mem_avail_gb":  raw["mem_avail_gb"],
        "mem_total_gb":  raw["mem_total_gb"],
        "swap_avail_gb": raw["swap_avail_gb"],
        "swap_total_gb": raw["swap_total_gb"],
        "load_1m":       raw["load_1m"],
        "load_5m":       raw["load_5m"],
        "load_15m":      raw["load_15m"],
        "controller":    raw["controller"],
        "raw_json":      json.dumps(raw, ensure_ascii=False, default=str),
    }


def _normalize_job(grid_name: str, raw: Dict) -> Dict:
    return {
        "collected_at":     datetime.now(_KST),
        "grid_name":        grid_name,
        "job_id":           raw["job_id"],
        "user":             raw["user"],
        "status":           raw["status"],
        "project":          raw["project"],
        "gpu_per_node":     raw["gpu_per_node"],
        "num_nodes":        raw["num_nodes"],
        "total_gpus":       raw["total_gpus"],
        "submit_time":      raw["submit_time"],
        "start_time":       raw["start_time"],
        "end_time":         raw["end_time"],
        "submit_ts":        raw["submit_ts"],
        "resources":        raw["resources"],
        "gpu_indices_raw":  raw["gpu_indices_raw"],
        "gpu_allocations":  json.dumps(raw["gpu_allocations"], ensure_ascii=False),
        "properties_json":  json.dumps(raw["properties"], ensure_ascii=False),
        "raw_json":         json.dumps(raw, ensure_ascii=False, default=str),
    }


# ═══════════════════════════════════════════════════════════════════════
#  S2Adapter
# ═══════════════════════════════════════════════════════════════════════

class S2Adapter:
    """S2 batch scheduler metadata adapter.

    Collection strategy
    -------------------
    - ``collect_jobs_active()``: full collection of Running + Queued  (default every 5 min)
    - ``collect_jobs_completed()``: Done/Failed/Stopped **end_time window** collection  (default every 10 min)
      - ``end_time > (last_end_ts - overlap)`` collects only recently finished jobs
      - overlap (10 min) prevents boundary gaps; duplicates are removed by ReplacingMergeTree
      - last_end_ts is recovered from ClickHouse on pod restart
    """

    _COMPLETED_STATUSES = "Done", "Failed", "Stopped"
    _COMPLETED_SEL_STATUS = " || ".join(f"status=={s}" for s in _COMPLETED_STATUSES)

    def __init__(
        self,
        bmi_url: str,
        phd_bin_template: str,
        http_proxy_addr: str,
        ssh_jump_host: str,
        ssh_login_host: str,
        writer,
        ssh_user: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        target_grids: Optional[List[str]] = None,
    ):
        self.writer = writer
        self._target_grids = target_grids

        proxies = {"http": http_proxy_addr, "https": http_proxy_addr}
        self._bmi = _BMIClient(bmi_url, proxies)
        self._phd = _SSHPHDRunner(
            phd_bin_template=phd_bin_template,
            jump_host=ssh_jump_host,
            login_host=ssh_login_host,
            ssh_user=ssh_user,
            ssh_key_path=ssh_key_path,
        )

        self._grid_map: Dict[str, str] = {}

        # grid → end_time of the last collected completed job (Unix timestamp)
        self._last_end_ts: Dict[str, float] = {}

    # ── Grid discovery ───────────────────────────────────────────────────

    def refresh_grids(self):
        try:
            all_grids = self._bmi.get_grids()
            targets = self._target_grids or all_grids
            new_map: Dict[str, str] = {}
            for g in targets:
                if g not in all_grids:
                    logger.warning("target grid '%s' not in BMI — skipped", g)
                    continue
                # The portal spec is informational; a grid without portal
                # pnodes must still be collected.
                spec = self._bmi.get_portal_spec(g)
                if not spec:
                    logger.warning("grid '%s' has no portal pnodes", g)
                new_map[g] = spec
            self._grid_map = new_map
            logger.info("grid map refreshed: %s", self._grid_map)
        except Exception as e:
            logger.error("refresh_grids failed: %s", e)

    def _ensure_grids(self):
        if not self._grid_map:
            self.refresh_grids()

    # ── last_end_ts management ───────────────────────────────────────────

    def _recover_last_end_ts(self, grid_name: str) -> float:
        """Query the maximum end_time among completed jobs for the grid from ClickHouse.

        On pod restart the in-memory _last_end_ts is empty, so the last value
        is recovered from the DB.
        Returns 0.0 on query failure → falls back to full collection on the first cycle.
        """
        statuses = ", ".join(f"'{s}'" for s in self._COMPLETED_STATUSES)
        # Unqualified table name -> resolves to the writer's configured database
        # (same DB the writes go to). Watermark is the latest end_time, not submit_ts.
        query = (
            f"SELECT max(toUnixTimestamp(end_time)) FROM s2_jobs FINAL "
            f"WHERE grid_name = %(grid)s AND status IN ({statuses}) "
            f"AND end_time IS NOT NULL"
        )
        try:
            result = self.writer.query(query, {"grid": grid_name})
            val = result[0][0] if result and result[0] else 0
            ts = float(val) if val else 0.0
            if ts > 0:
                logger.info(
                    "Recovered last_end_ts from CH: grid=%s, ts=%.1f (%s)",
                    grid_name, ts, _ts_to_datetime(ts),
                )
            return ts
        except Exception as e:
            logger.warning(
                "Failed to recover last_end_ts from CH (grid=%s): %s — will fetch all",
                grid_name, e,
            )
            return 0.0

    def _get_last_end_ts(self, grid_name: str) -> float:
        """Return the grid's last_end_ts.  Recover from CH if not present."""
        if grid_name not in self._last_end_ts:
            self._last_end_ts[grid_name] = self._recover_last_end_ts(grid_name)
        return self._last_end_ts[grid_name]

    def _update_last_end_ts(self, grid_name: str, jobs: List[Dict]):
        """Update with the maximum end_ts among the collected completed jobs."""
        if not jobs:
            return
        end_timestamps = [j["end_ts"] for j in jobs if j.get("end_ts", 0) > 0]
        if not end_timestamps:
            return
        max_ts = max(end_timestamps)
        current = self._last_end_ts.get(grid_name, 0.0)
        if max_ts > current:
            self._last_end_ts[grid_name] = max_ts
            logger.debug(
                "last_end_ts updated: grid=%s, %.1f → %.1f", grid_name, current, max_ts,
            )

    # ── Nodes ────────────────────────────────────────────────────────────

    def collect_nodes(self):
        """Collect the computing-node status of every grid → s2_nodes."""
        self._ensure_grids()
        for grid_name in self._grid_map:
            try:
                raw = self._phd.run(["host"], grid_name)
                parsed = _parse_phd_host(raw)
                rows = [_normalize_node(grid_name, n) for n in parsed]
                if rows:
                    self.writer.insert("s2_nodes", rows)
                logger.debug("s2 nodes: grid=%s, rows=%d", grid_name, len(rows))
            except Exception as e:
                logger.error("s2 collect_nodes failed (grid=%s): %s", grid_name, e)

    # ── Active jobs (Running + Queued) ───────────────────────────────────

    def collect_jobs_active(self):
        """Collect all Running + Queued jobs → s2_jobs.

        After collection, look up in ClickHouse the jobs that were previously
        Running/Queued but no longer appear in this collection, and close them
        (Running → Done, Queued → Stopped).
        (Handles the case where a job went Running → Done → purge and the
        collection opportunity was missed.)
        """
        self._ensure_grids()
        for grid_name in self._grid_map:
            active_job_keys: set = set()  # (job_id, submit_ts)
            collection_ok = True

            for flag, label in [("-r", "Running"), ("-q", "Queued")]:
                try:
                    raw = self._phd.run(
                        ["list", "-a", flag, "-O", _LIST_FORMAT],
                        grid_name,
                    )
                    parsed = _parse_phd_list(raw)
                    rows = [_normalize_job(grid_name, j) for j in parsed]
                    if rows:
                        self.writer.insert("s2_jobs", rows)
                    for j in parsed:
                        active_job_keys.add((j["job_id"], j["submit_ts"]))
                    logger.debug(
                        "s2 jobs [%s]: grid=%s, rows=%d", label, grid_name, len(rows),
                    )
                except Exception as e:
                    logger.error(
                        "s2 collect_jobs_%s failed (grid=%s): %s", label, grid_name, e,
                    )
                    collection_ok = False

            # If any phd call failed (PhdCommandError or otherwise), do not
            # perform vanished detection — with a partial/empty active set a
            # network issue would close every active job.
            if collection_ok:
                self._close_vanished_jobs(grid_name, active_job_keys)

    # ── Close vanished jobs ──────────────────────────────────────────────

    def _close_vanished_jobs(self, grid_name: str, active_job_keys: set):
        """Close the jobs that are Running/Queued in CH but no longer present in phd.

        A vanished Running job finished → Done. A vanished Queued job never
        ran (cancelled/removed) → Stopped, so completion metrics are not
        inflated.

        Parameters
        ----------
        grid_name : str
        active_job_keys : set of (job_id, submit_ts)
            The active job keys confirmed from phd in this collection.
        """
        try:
            query = (
                "SELECT job_id, user, project, gpu_per_node, num_nodes, "
                "       total_gpus, submit_time, start_time, submit_ts, "
                "       resources, gpu_indices_raw, gpu_allocations, "
                "       properties_json, status "
                "FROM s2_jobs FINAL "
                "WHERE grid_name = %(grid)s "
                "  AND status IN ('Running', 'Queued')"
            )
            ch_rows = self.writer.query(query, {"grid": grid_name})
        except Exception as e:
            logger.warning("_close_vanished_jobs CH query failed (grid=%s): %s", grid_name, e)
            return

        if not ch_rows:
            return

        now = datetime.now(_KST)
        closed_rows = []

        for row in ch_rows:
            job_id = row[0]
            submit_ts = float(row[8]) if row[8] else 0.0
            key = (job_id, submit_ts)

            if key not in active_job_keys:
                prior_status = row[13]
                closed_rows.append({
                    "collected_at":     now,
                    "grid_name":        grid_name,
                    "job_id":           job_id,
                    "user":             row[1] or "",
                    "status":           "Done" if prior_status == "Running" else "Stopped",
                    "project":          row[2] or "",
                    "gpu_per_node":     row[3] or 0,
                    "num_nodes":        row[4] or 0,
                    "total_gpus":       row[5] or 0,
                    "submit_time":      row[6],
                    "start_time":       row[7],
                    "end_time":         now,
                    "submit_ts":        submit_ts,
                    "resources":        row[9] or [],
                    "gpu_indices_raw":  row[10] or "",
                    "gpu_allocations":  row[11] or "[]",
                    "properties_json":  row[12] or "{}",
                    "raw_json":         json.dumps({
                        "_closed_by": "vanished_detection",
                        "prior_status": prior_status,
                    }),
                })

        if closed_rows:
            self.writer.insert("s2_jobs", closed_rows)
            logger.info(
                "Closed %d vanished jobs: grid=%s, job_ids=%s",
                len(closed_rows), grid_name,
                [r["job_id"] for r in closed_rows[:10]],
            )

    # ── Completed jobs (Done + Failed + Stopped) ─────────────────────────

    def collect_jobs_completed(self):
        """Collect completed jobs by end_time window → s2_jobs.

        ``phd list -a -sel "(status==Done || ...) && end_time > TS"``

        TS = last_end_ts - overlap (10 min).
        If last_end_ts is 0 (first collection / recovery failure), collect only
        the last 7 days to prevent OOM.
        overlap prevents boundary gaps; duplicates are removed by ReplacingMergeTree.
        """
        self._ensure_grids()
        for grid_name in self._grid_map:
            try:
                last_ts = self._get_last_end_ts(grid_name)

                if last_ts > 0:
                    # Normal: collect starting from overlap before the last end_time
                    window_ts = last_ts - _COMPLETED_OVERLAP_SECS
                else:
                    # First collection or recovery failure: collect only the last 7 days (prevents OOM)
                    window_ts = time.time() - _INITIAL_LOOKBACK_SECS
                    logger.info(
                        "First completed collection for grid=%s, "
                        "limiting to last %d days",
                        grid_name, _INITIAL_LOOKBACK_SECS // 86400,
                    )

                sel_expr = (
                    f"({self._COMPLETED_SEL_STATUS}) && end_time > {window_ts}"
                )
                raw = self._phd.run(
                    ["list", "-a", "-sel", sel_expr, "-O", _LIST_FORMAT],
                    grid_name,
                )
                parsed = _parse_phd_list(raw)
                rows = [_normalize_job(grid_name, j) for j in parsed]
                if rows:
                    self.writer.insert("s2_jobs", rows)
                    # Advance the watermark only once the rows are persisted:
                    # buffered rows are invisible to _recover_last_end_ts, so
                    # a crash would otherwise skip them on restart. On flush
                    # failure the window simply re-covers them next cycle.
                    if self.writer.flush():
                        self._update_last_end_ts(grid_name, parsed)
                logger.debug(
                    "s2 jobs [Completed]: grid=%s, rows=%d (end_time > %.1f)",
                    grid_name, len(rows), window_ts,
                )
            except Exception as e:
                logger.error(
                    "s2 collect_jobs_completed failed (grid=%s): %s", grid_name, e,
                )
