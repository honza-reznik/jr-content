#!/usr/bin/env bash
# Sprint P-023: spustí testera na seznam karet. Použití: tester.sh <sonnet|luna> <běh> K-01 [K-09 ...]
# Vypíše PID testera. Výsledky: KB vyvoj/testy/vysledky/, snímky: jr-content-wt/TEST/ops/screenshoty/sprint-mvp/.
set -euo pipefail
who=$1; run=$2; shift 2; cards="$*"
KB=/home/honza/jarvis/knowledge/projects/jr-content
LOG=/home/honza/jr-content/ops/logs/sprint-tester-$run.log
mkdir -p "$KB/vyvoj/testy/vysledky"
task="Jsi tester sprintu MVP projektu JR Content. Přečti $KB/vyvoj/testy/karty/README.md (pravidla, prostředí, formát zápisu – dodrž je přesně) a pak postupně projdi karty: $cards (soubory $KB/vyvoj/testy/karty/<karta>.md) v tomto pořadí. Označení běhu: $run. Prohlížeč ovládej jen nástroji jrc-playwright (Chromium v kontejneru jrc-dev:jrc-t v síti počítače; aplikace je na http://127.0.0.1:13603/jrc/ – jinou adresu ani hlavičky nepoužívej; když stránka nejde otevřít, zapiš NEJDE PROVÉST a skonči kartu). Přihlašovací údaje čti ze souboru /home/honza/jr-content-import/sprint-ucty.env a nikam je nevypisuj. Strukturu karet neměň; jak krok provést, vymysli sám. Ke každému kroku zápis co jsi udělal + snímek + výsledek. Výsledky každé karty zapiš do $KB/vyvoj/testy/vysledky/<karta>-$run.md a nálezy připiš do $KB/vyvoj/testy/nalezy.md. Nic neopravuj v kódu. Na konci vypiš krátký souhrn (karta: PROŠLO/NEPROŠLO/NEJDE PROVÉST počty, hlavní nálezy)."
cd /home/honza/jr-content
if [[ $who == sonnet ]]; then
  nohup bash -c "printf '%s' \"\$0\" | timeout 10800 /home/honza/.local/bin/claude -p --model claude-sonnet-5-5 --effort high --mcp-config /home/honza/jr-content/ops/mcp/jrc-t-playwright.json --strict-mcp-config --add-dir $KB --add-dir /home/honza/jr-content-import --allowedTools 'mcp__jrc-playwright__*,Read,Edit(/$KB/vyvoj/testy/**)'" "$task" > "$LOG" 2>&1 &
else
  nohup timeout 10800 /home/honza/.local/bin/codex exec -m gpt-6-luna -c 'model_reasoning_effort="high"' -s workspace-write \
    -c 'mcp_servers.playwright.enabled=false' -c 'mcp_servers.jrc_playwright.command="docker"' \
    -c 'mcp_servers.jrc_playwright.args=["run","--rm","-i","--pull","never","--network","host","--user","1000:1000","-v","/home/honza/jr-content-wt/TEST:/workspace","-w","/workspace","jrc-dev:jrc-t","jrc-playwright-mcp"]' \
    -c 'mcp_servers.jrc_playwright.default_tools_approval_mode="approve"' -c sandbox_workspace_write.network_access=true \
    --skip-git-repo-check -C /home/honza/jr-content-wt/TEST --add-dir "$KB" --add-dir /home/honza/jr-content-import \
    -o "/home/honza/jr-content/ops/logs/sprint-tester-$run-posledni.txt" "$task" < /dev/null > "$LOG" 2>&1 &
fi
echo $!
