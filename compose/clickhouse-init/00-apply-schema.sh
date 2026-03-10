#!/bin/sh
set -eu

schema_dir="/opt/gpu-mon/schemas"
tmp_sql="/tmp/gpu-mon-compose-schema.sql"

if [ ! -d "$schema_dir" ]; then
  echo "Schema directory not found: $schema_dir" >&2
  exit 1
fi

# Docker Compose uses a single-node ClickHouse instance, so strip the
# cluster-only clauses from the canonical DDL before applying it.
{
  for f in "$schema_dir"/*.sql; do
    [ -f "$f" ] || continue
    sed "s/ ON CLUSTER '{cluster}'//g" "$f"
    printf '\n'
  done
} > "$tmp_sql"

clickhouse client --multiquery < "$tmp_sql"
