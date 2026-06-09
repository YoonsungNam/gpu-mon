#!/usr/bin/env python3
"""validate_replication_topology.py — Guard against the "replicated topology, but
non-replicated tables" mismatch.

A ClickHouse cluster with ``layout.replicasCount > 1`` only provides real data HA if
its tables use a ``Replicated*`` engine (ReplicatedMergeTree, ReplicatedReplacingMergeTree,
...). With a plain MergeTree/ReplacingMergeTree the operator just provisions N replica
pods that hold INDEPENDENT, diverging copies — ``replicasCount > 1`` then buys nothing
but a false sense of HA. This guard fails when an environment declares
``replicasCount > 1`` while any schema in ``schemas/*.sql`` uses a non-replicated
MergeTree-family engine.

Usage:
    python3 scripts/validate_replication_topology.py [environment]   # default: homelab

Resolves the cluster layout from ``environments/<env>/clickhouse.yaml``, falling back to
``environments/<env>/clickhouse.yaml.example`` (so it also lints the corp.example
template, e.g. ``... corp.example``).

Env vars:
    WARN_ONLY=1   Report a mismatch (or a missing values file) as a warning and exit 0
                  (for an aspirational template that intentionally precedes a schema
                  migration). Without it, a missing values file for the requested
                  environment is a hard failure.

Stdlib only — no PyYAML / yq required.
"""
from __future__ import annotations

import glob
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# replicasCount lives at clickhouse.cluster.layout.replicasCount.
_LAYOUT_PATH = ("clickhouse", "cluster", "layout")
_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_]+)\s*:\s*(.*?)\s*$")

# A CREATE TABLE/VIEW statement's target name (best-effort; quoted names with spaces
# are reported partially, which does not affect detection).
_CREATE_RE = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:materialized\s+|temporary\s+)?(?:table|view)\s+"
    r"(?:if\s+not\s+exists\s+)?([`\"\w.]+)",
    re.IGNORECASE,
)
# Every ENGINE = <name> occurrence (case-insensitive; whitespace/newline tolerant).
_ENGINE_RE = re.compile(r"engine\s*=\s*([A-Za-z0-9_]+)", re.IGNORECASE)


def _strip_yaml_comment(line: str) -> str:
    """Remove a YAML inline/full-line ``#`` comment (best-effort, ignores quoting)."""
    if line.lstrip().startswith("#"):
        return ""
    return re.sub(r"\s+#.*$", "", line)


def effective_replicas(values_file: str) -> int:
    """Return clickhouse.cluster.layout.replicasCount (path-scoped), default 1."""
    if not os.path.isfile(values_file):
        return 1
    stack: list[tuple[int, str]] = []  # (indent, key) for the current ancestry
    with open(values_file, encoding="utf-8") as fh:
        for raw in fh:
            line = _strip_yaml_comment(raw.rstrip("\n"))
            if not line.strip():
                continue
            m = _KEY_RE.match(line)
            if not m:
                continue
            indent, key, value = len(m.group(1)), m.group(2), m.group(3)
            # Pop ancestors that are siblings/deeper than this key.
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parents = tuple(k for _, k in stack)
            if key == "replicasCount" and parents == _LAYOUT_PATH:
                vm = re.match(r"[\"']?(\d+)", value)
                if vm:
                    return int(vm.group(1), 10)  # base-10: tolerate leading zeros
            stack.append((indent, key))
    return 1


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)  # /* block */
    sql = re.sub(r"--[^\n]*", " ", sql)                     # -- line
    return sql


def _is_nonreplicated_mergetree(engine: str) -> bool:
    e = engine.lower()
    return e.endswith("mergetree") and not e.startswith("replicated")


def non_replicated_tables(sql: str) -> list[tuple[str, str]]:
    """Return [(table_name, engine)] for non-replicated MergeTree-family tables."""
    offenders: list[tuple[str, str]] = []
    for stmt in _strip_sql_comments(sql).split(";"):
        if "engine" not in stmt.lower():
            continue
        name_m = _CREATE_RE.search(stmt)
        name = name_m.group(1) if name_m else "?"
        for eng in _ENGINE_RE.findall(stmt):
            if _is_nonreplicated_mergetree(eng):
                offenders.append((name, eng))
    return offenders


def scan_schemas(schema_dir: str) -> list[tuple[str, str, str]]:
    """Return [(file, table, engine)] across schema_dir/*.sql (empty if none)."""
    offenders: list[tuple[str, str, str]] = []
    for path in sorted(glob.glob(os.path.join(schema_dir, "*.sql"))):
        with open(path, encoding="utf-8") as fh:
            for name, eng in non_replicated_tables(fh.read()):
                offenders.append((os.path.basename(path), name, eng))
    return offenders


def _resolve_values_file(env: str) -> str | None:
    base = os.path.join(REPO_ROOT, "environments", env, "clickhouse.yaml")
    if os.path.isfile(base):
        return base
    if os.path.isfile(base + ".example"):
        return base + ".example"
    return None


def main(argv: list[str]) -> int:
    env = argv[1] if len(argv) > 1 else "homelab"
    warn_only = os.environ.get("WARN_ONLY", "0") == "1"
    schema_dir = os.path.join(REPO_ROOT, "schemas")

    print(f"=== Replication-topology guard: environment '{env}' ===")

    values_file = _resolve_values_file(env)
    if values_file is None:
        msg = (f"no clickhouse values for '{env}' "
               f"(looked for environments/{env}/clickhouse.yaml[.example]).")
        if warn_only:
            print(f"SKIP (WARN_ONLY=1): {msg}")
            return 0
        # A guard that silently passes when its input disappears is a soft-fail:
        # a renamed/removed values file would mask a real misconfiguration. Treat a
        # missing file for a deployable environment as an error.
        print(f"FAILED: {msg}")
        print("A deployable environment must ship a clickhouse values file for the "
              "guard to check it. Set WARN_ONLY=1 to downgrade this to a skip.")
        return 1

    replicas = effective_replicas(values_file)
    rel = os.path.relpath(values_file, REPO_ROOT)
    print(f"  layout.replicasCount = {replicas}  (from {rel})")

    if replicas <= 1:
        print("OK: replicasCount <= 1 — single-replica topology needs no replicated engines.")
        return 0

    offenders = scan_schemas(schema_dir)
    if not offenders:
        print(f"OK: replicasCount={replicas} and all schema tables use Replicated* engines.")
        return 0

    print(f"\nFound replicasCount={replicas} with NON-replicated table engine(s):")
    for fname, table, engine in offenders:
        print(f"  - {table:<28} {engine:<24} ({fname})")
    print(
        "\nreplicasCount > 1 gives real HA only with Replicated* engines. Either:\n"
        f"  (a) set clickhouse.cluster.layout.replicasCount: 1 for '{env}' until schemas "
        "are replicated, or\n"
        "  (b) convert the tables above to Replicated*MergeTree (and add a Distributed "
        "table for cross-shard reads)."
    )

    if warn_only:
        print("\nWARNING (WARN_ONLY=1): reported, not failing.")
        return 0
    print("\nFAILED: replicated topology declared but tables are not replicated.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
