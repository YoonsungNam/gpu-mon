#!/bin/sh
set -eu

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
schema_dir="$repo_root/schemas"
output_dir="$repo_root/compose/clickhouse-init"
output_file="$output_dir/00-gpu-monitoring.sql"

mkdir -p "$output_dir"

{
  printf -- "-- Generated from canonical schemas/ for Docker Compose.\n"
  printf -- "-- Single-node Compose strips cluster-only clauses.\n\n"
  for f in "$schema_dir"/*.sql; do
    [ -f "$f" ] || continue
    sed "s/ ON CLUSTER '{cluster}'//g" "$f"
    printf '\n'
  done
} > "$output_file"
