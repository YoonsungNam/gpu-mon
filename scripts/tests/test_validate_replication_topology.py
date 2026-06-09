"""Tests for scripts/validate_replication_topology.py.

Covers the red-team cases that broke the first (awk) draft: multiline ENGINE,
case-insensitive keywords, multiple statements per line, block comments,
path-scoped replicasCount, quoted/odd values, and the empty-schemas edge.
"""
import importlib.util
import os

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "validate_replication_topology.py")
_spec = importlib.util.spec_from_file_location("vrt", _SCRIPT)
vrt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vrt)


# ── engine detection ────────────────────────────────────────────────────────
@pytest.mark.parametrize("sql", [
    "CREATE TABLE t (a Int32) ENGINE = MergeTree() ORDER BY a;",
    "CREATE TABLE t (a Int32) ENGINE=MergeTree ORDER BY a;",            # no parens
    "CREATE TABLE t (a Int32) ENGINE\n  =\n  MergeTree() ORDER BY a;",  # multiline
    "create table t (a Int32) engine = mergetree() order by a;",        # lowercase
    "CREATE TABLE t (a Int32) Engine = MergeTree() ORDER BY a;",        # mixed case
    "CREATE TABLE t (a Int32) ENGINE = ReplacingMergeTree(v) ORDER BY a;",
    "CREATE TABLE t (a Int32) ENGINE = SummingMergeTree() ORDER BY a;",
])
def test_flags_non_replicated_mergetree(sql):
    assert vrt.non_replicated_tables(sql), f"should flag: {sql!r}"


@pytest.mark.parametrize("sql", [
    "CREATE TABLE t (a Int32) ENGINE = ReplicatedMergeTree('/p','{replica}') ORDER BY a;",
    "CREATE TABLE t (a Int32) ENGINE = ReplicatedReplacingMergeTree('/p','{replica}',v) ORDER BY a;",
    "CREATE TABLE t (a Int32) ENGINE = Distributed(c, db, t, rand());",
    "CREATE TABLE t (a Int32) ENGINE = Null;",
    "CREATE TABLE t (a Int32) ENGINE = Memory;",
    "CREATE TABLE t (k String, v String) ENGINE = Kafka;",
    "-- CREATE TABLE t (a Int32) ENGINE = MergeTree();",               # line comment
    "/* ENGINE = MergeTree */ CREATE TABLE t (a Int32) "
    "ENGINE = ReplicatedMergeTree('/p','{r}') ORDER BY a;",             # block comment
])
def test_does_not_flag(sql):
    assert vrt.non_replicated_tables(sql) == [], f"should NOT flag: {sql!r}"


def test_two_statements_one_line_plain_first():
    sql = ("CREATE TABLE bad (a Int32) ENGINE = MergeTree(); "
           "CREATE TABLE good (a Int32) ENGINE = ReplicatedMergeTree('/p','{r}');")
    flagged = vrt.non_replicated_tables(sql)
    assert [n for n, _ in flagged] == ["bad"]


def test_mixed_file_reports_only_plain_with_names():
    sql = (
        "CREATE TABLE db.plain (a Int32) ENGINE = ReplacingMergeTree(a) ORDER BY a;\n"
        "CREATE TABLE db.repl  (a Int32) ENGINE = ReplicatedMergeTree('/p','{r}') ORDER BY a;\n"
    )
    flagged = vrt.non_replicated_tables(sql)
    assert flagged == [("db.plain", "ReplacingMergeTree")]


# ── replicasCount parsing (path-scoped) ─────────────────────────────────────
def _write(tmp_path, body):
    p = tmp_path / "clickhouse.yaml"
    p.write_text(body)
    return str(p)


def test_replicas_basic(tmp_path):
    f = _write(tmp_path, "clickhouse:\n  cluster:\n    layout:\n      replicasCount: 2\n")
    assert vrt.effective_replicas(f) == 2


def test_replicas_default_when_absent(tmp_path):
    f = _write(tmp_path, "clickhouse:\n  cluster:\n    layout:\n      shardsCount: 2\n")
    assert vrt.effective_replicas(f) == 1


def test_replicas_inline_comment(tmp_path):
    f = _write(tmp_path, "clickhouse:\n  cluster:\n    layout:\n      replicasCount: 2  # HA\n")
    assert vrt.effective_replicas(f) == 2


def test_replicas_quoted_value(tmp_path):
    f = _write(tmp_path, 'clickhouse:\n  cluster:\n    layout:\n      replicasCount: "2"\n')
    assert vrt.effective_replicas(f) == 2


def test_replicas_leading_zero(tmp_path):
    f = _write(tmp_path, "clickhouse:\n  cluster:\n    layout:\n      replicasCount: 08\n")
    assert vrt.effective_replicas(f) == 8


def test_replicas_ignores_decoy_outside_layout(tmp_path):
    # A sibling block lists replicasCount: 1 FIRST; the real layout value is 2.
    f = _write(tmp_path,
               "somecomponent:\n  replicasCount: 1\n"
               "clickhouse:\n  cluster:\n    layout:\n      replicasCount: 2\n")
    assert vrt.effective_replicas(f) == 2


def test_replicas_ignores_comment_only_key(tmp_path):
    f = _write(tmp_path,
               "clickhouse:\n  cluster:\n    layout:\n"
               "      # replicasCount: 2 (planned)\n      replicasCount: 1\n")
    assert vrt.effective_replicas(f) == 1


# ── scan + missing/empty handling ───────────────────────────────────────────
def test_scan_empty_dir_no_crash(tmp_path):
    assert vrt.scan_schemas(str(tmp_path)) == []


def test_scan_dir_without_sql(tmp_path):
    (tmp_path / "readme.md").write_text("not sql")
    assert vrt.scan_schemas(str(tmp_path)) == []


# ── missing values file (soft-fail guard) ───────────────────────────────────
def test_main_missing_env_hard_fails(monkeypatch):
    # A deployable env whose values file is absent must fail, not silently pass.
    monkeypatch.delenv("WARN_ONLY", raising=False)
    assert vrt.main(["prog", "does-not-exist-env"]) == 1


def test_main_missing_env_warn_only_skips(monkeypatch):
    monkeypatch.setenv("WARN_ONLY", "1")
    assert vrt.main(["prog", "does-not-exist-env"]) == 0
