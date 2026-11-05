#!/usr/bin/env bash
# Sprint P-023: spustí Codex Sol kodéra na blok (navazuje na worktree). Použití: codex-coder.sh <ID> [model]. Vypíše PID.
set -euo pipefail
id=$1; m=${2:-gpt-6.1-sol}; KB=/home/honza/jarvis/knowledge/projects/jr-content; WT=/home/honza/jr-content-wt/$id
proj=jrc-$(echo "$id" | tr 'A-Z' 'a-z' | tr -d '-')
cd /home/honza/jr-content
nohup /home/honza/.local/bin/codex exec -m "$m" -c 'model_reasoning_effort="high"' -s workspace-write -c sandbox_workspace_write.network_access=true --skip-git-repo-check -C "$WT" --add-dir "$KB" -o "ops/logs/$id-B-posledni.txt" "Jsi kodér B projektu JR Content. Přečti /home/honza/jr-content/AGENTS.md, $KB/vyvoj/sprint-mvp-2026-11.md, zadání $KB/vyvoj/bloky/$id.md včetně průběhu (můžeš navazovat na rozpracovanou práci jiného kodéra – zkontroluj commity ve větvi a worktree $WT) a $KB/vyvoj/testy/prostredi-jrc-t.md. Pracuj ve worktree $WT, vlastní projekt $proj. Stahovat co nejméně: žádné sestavování obrazů, jen obrazy *:jrc přeznačené docker tag a compose --no-build --pull never; migrace smíš. Průběh do sekce 2, testy do sekce 3, výsledek do sekce 4. Commit nedělej. Hlavní jrc ani jrc-t neměň. Cílené testy + Chromium jen v Dockeru; každý skript prohlížeče a node spouštěj s timeoutem a omezením paměti (node --max-old-space-size=2048, celý běh nejvýš 10 min) – 5. 11. skript P1-B14 zabral 19 GB RAM a ohrozil počítač. Na konci uklid svůj projekt." > "ops/logs/$id-B-run-$(date +%H%M).log" 2>&1 &
echo $!
