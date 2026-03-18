#!/usr/bin/env bash
# validate-values-keys.sh — Detect Helm values override keys that don't exist
# in the chart's defaults. Reads chart/version/values mapping from helmfile
# automatically, so no hardcoded list to maintain.
#
# Usage:
#   ./scripts/validate-values-keys.sh [environment]   # default: homelab
#
# Prerequisites: helmfile, helm, yq (v4)

set -euo pipefail

ENV="${1:-homelab}"
exit_code=0
warnings=0

echo "=== Validating values keys for environment: $ENV ==="

# Render helmfile and extract releases as JSON
rendered=$(helmfile -e "$ENV" build --quiet 2>/dev/null)
releases=$(echo "$rendered" | yq -o=json '[.releases[] | {
  "name": .name,
  "chart": .chart,
  "version": .version,
  "values": .values
}]')

count=$(echo "$releases" | yq 'length')

for i in $(seq 0 $((count - 1))); do
  name=$(echo "$releases"  | yq -r ".[$i].name")
  chart=$(echo "$releases" | yq -r ".[$i].chart")
  version=$(echo "$releases" | yq -r ".[$i].version // \"\"")

  # Get chart default keys
  if [[ "$chart" == charts/* ]]; then
    # Custom chart — read local values.yaml
    defaults_file="$chart/values.yaml"
    if [[ ! -f "$defaults_file" ]]; then
      echo "SKIP [$name]: no values.yaml in $chart"
      continue
    fi
    chart_keys=$(yq 'keys | .[]' "$defaults_file" 2>/dev/null | sort)
    chart_label="$chart (local)"
  else
    # OSS chart — fetch defaults via helm show values
    if [[ -z "$version" ]]; then
      echo "SKIP [$name]: no version pinned for $chart"
      continue
    fi
    chart_defaults=$(helm show values "$chart" --version "$version" 2>/dev/null)
    active_keys=$(echo "$chart_defaults" | yq 'keys | .[]' 2>/dev/null)
    # Also capture commented-out top-level keys (e.g. "# adminPassword: ...")
    commented_keys=$(echo "$chart_defaults" \
      | grep -E '^# [a-zA-Z][a-zA-Z0-9_-]*:' | sed 's/^# //; s/:.*//')
    chart_keys=$(printf '%s\n%s\n' "$active_keys" "$commented_keys" | grep -v '^$' | sort -u)
    chart_label="$chart@$version"
  fi

  if [[ -z "$chart_keys" ]]; then
    echo "SKIP [$name]: could not extract keys from $chart_label"
    continue
  fi

  # Check each values file for this release
  values_json=$(echo "$releases" | yq -o=json ".[$i].values // []")
  values_count=$(echo "$values_json" | yq 'length')

  for j in $(seq 0 $((values_count - 1))); do
    vf=$(echo "$values_json" | yq -r ".[$j]")

    # Skip inline values (maps/objects, not file paths)
    [[ -f "$vf" ]] || continue

    override_keys=$(yq 'keys | .[]' "$vf" 2>/dev/null | sort)
    [[ -z "$override_keys" ]] && continue

    unknown=$(comm -23 <(echo "$override_keys") <(echo "$chart_keys"))
    if [[ -n "$unknown" ]]; then
      echo ""
      echo "WARNING [$name]: $vf sets keys not found in $chart_label defaults:"
      echo "$unknown" | sed 's/^/  - /'
      warnings=$((warnings + 1))
      exit_code=1
    fi
  done
done

echo ""
if [[ $exit_code -eq 0 ]]; then
  echo "OK: All override keys match chart defaults ($count releases checked)."
else
  echo "FAILED: $warnings file(s) have unknown keys. See warnings above."
fi

exit $exit_code
