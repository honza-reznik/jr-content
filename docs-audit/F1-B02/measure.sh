#!/usr/bin/env bash
# Spustit na hostiteli v kořeni worktree souběžně s audit-api.py.
# stats.tsv: kontejnery tohoto compose projektu (100 % CPU = jedno logické jádro).
# host.tsv: zátěž celého počítače (load average) a součet CPU ostatních kontejnerů,
# aby šlo poznat, co běželo souběžně.
set -euo pipefail
output=audit/results/stats.tsv
host=audit/results/host.tsv
printf 'time\tphase\tcontainer\tcpu\tram\tmemory_percent\n' > "$output"
printf 'time\tphase\tload1\tother_containers_cpu_sum\tbusiest_other\n' > "$host"
while [[ $(cat audit/results/phase 2>/dev/null || true) != done ]]; do
  phase=$(cat audit/results/phase 2>/dev/null || printf idle)
  stamp=$(date --iso-8601=seconds)
  all=$(docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}')
  grep '^jrc-audit-freeframe-' <<<"$all" |
    while IFS= read -r row; do printf '%s\t%s\t%s\n' "$stamp" "$phase" "$row"; done >> "$output"
  others=$(grep -v '^jrc-audit-freeframe-' <<<"$all" | tr -d '%' | sort -t$'\t' -k2 -rn)
  sum=$(awk -F'\t' '{s+=$2} END {printf "%.1f", s}' <<<"$others")
  busiest=$(head -1 <<<"$others" | awk -F'\t' '{printf "%s %s%%", $1, $2}')
  printf '%s\t%s\t%s\t%s\t%s\n' "$stamp" "$phase" "$(cut -d' ' -f1 /proc/loadavg)" "$sum" "$busiest" >> "$host"
done
