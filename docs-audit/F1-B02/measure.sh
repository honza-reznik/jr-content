#!/usr/bin/env bash
# Spustit na hostiteli v kořeni worktree souběžně s audit-api.py.
set -euo pipefail
output=audit/results/stats.tsv
printf 'time\tphase\tcontainer\tcpu\tram\tmemory_percent\n' > "$output"
while [[ $(cat audit/results/phase 2>/dev/null || true) != done ]]; do
  phase=$(cat audit/results/phase 2>/dev/null || printf idle)
  stamp=$(date --iso-8601=seconds)
  ids=$(docker ps -q --filter label=com.docker.compose.project=jrc-audit-freeframe)
  if [[ -n "$ids" ]]; then
    docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' $ids |
      while IFS= read -r row; do printf '%s\t%s\t%s\n' "$stamp" "$phase" "$row"; done >> "$output"
  fi
  sleep 2
done
