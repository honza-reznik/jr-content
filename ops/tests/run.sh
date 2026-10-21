#!/usr/bin/env bash
# Testy ops/tick.sh s podvrženými příkazy claude a codex (blok F0-B05).
# Spouštěj jen v Dockeru: make -f ops/Makefile test-tick
# Testy nikdy nespouštějí skutečné claude ani codex: konfigurace míří na falešné skripty
# v dočasném adresáři a skutečné cesty projektu se nepoužijí.

set -uo pipefail

if [[ ${TICK_TEST_IN_DOCKER:-} != 1 ]]; then
  echo "Testy se spouštějí jen v Dockeru: make -f ops/Makefile test-tick" >&2
  exit 2
fi

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TICK=$REPO/ops/tick.sh
export TZ=Europe/Prague
unset ANTHROPIC_API_KEY OPENAI_API_KEY TICK_NOW TICK_CONF

PASS=0
FAIL=0
CUR=""
CUR_FAILED=0

# --- Pomocné funkce -----------------------------------------------------------

epoch() { date -d "$1" +%s; }

# Vytvoří čisté prostředí pro jeden test v $T.
setup() {
  T=$(mktemp -d)
  mkdir -p "$T/bin" "$T/fake" "$T/proj/ops" "$T/kb/vyvoj" "$T/wt/F0-B03" "$T/codex/2026/10/21"
  echo "# prompt PM (test)" >"$T/kb/vyvoj/prompt-projektovy-manazer.md"
  stav "aktivni=F0-B03 koder=B"

  cat >"$T/bin/claude" <<EOF
#!/usr/bin/env bash
F=$T/fake
printf 'claude' >>"\$F/calls.log"; printf ' [%s]' "\$@" >>"\$F/calls.log"; echo >>"\$F/calls.log"
echo "claude-cwd=\$PWD stdin=\$(readlink /proc/self/fd/0)" >>"\$F/calls.log"
if [[ \${1:-} == -p && \${2:-} == /usage ]]; then
  [[ -f \$F/usage.txt ]] && cat "\$F/usage.txt"
  exit "\$(cat "\$F/usage.rc" 2>/dev/null || echo 0)"
fi
[[ -f \$F/pm.sleep ]] && sleep "\$(cat "\$F/pm.sleep")"
cat "\$F/pm.out" 2>/dev/null
exit "\$(cat "\$F/pm.rc" 2>/dev/null || echo 0)"
EOF
  cat >"$T/bin/codex" <<EOF
#!/usr/bin/env bash
F=$T/fake
printf 'codex' >>"\$F/calls.log"; printf ' [%s]' "\$@" >>"\$F/calls.log"; echo >>"\$F/calls.log"
echo "codex-cwd=\$PWD stdin=\$(readlink /proc/self/fd/0)" >>"\$F/calls.log"
echo "falešný codex pracuje"
exit 0
EOF
  chmod +x "$T/bin/claude" "$T/bin/codex"
  : >"$T/fake/calls.log"

  # Konfigurace: skutečná ops/tick.conf, cesty a programy přesměrované do $T.
  cat >"$T/tick.conf" <<EOF
source "$REPO/ops/tick.conf"
PROJECT_DIR=$T/proj
KB_DIR=$T/kb
WT_ROOT=$T/wt
STAV_FILE=$T/kb/vyvoj/stav.md
PM_PROMPT_FILE=$T/kb/vyvoj/prompt-projektovy-manazer.md
PERMIT_FILE=$T/proj/ops/povoleni-den
LOCK_FILE=$T/proj/ops/tick.lock
LOG_DIR=$T/proj/ops/logs
CODEX_SESSIONS_DIR=$T/codex
CLAUDE_BIN=$T/bin/claude
CODEX_BIN=$T/bin/codex
EOF
  # Výchozí usage: Claude okno běží s obnovou 2:09, týden 70 %; Codex obnova 22.10. 0:30.
  claude_usage "Oct 22, 2:09am (Europe/Prague)" 70
  codex_usage "$(epoch '2026-10-22 00:30')"
}

teardown() { rm -rf "$T"; }

conf() { echo "$*" >>"$T/tick.conf"; }
stav() { printf '# STAV\n\n<!-- tick: %s -->\n' "$1" >"$T/kb/vyvoj/stav.md"; }

# claude_usage "<obnova session nebo ->" <týden využito %>
claude_usage() {
  local sess="Current session: 18% used · resets $1"
  [[ $1 == - ]] && sess="Current session: 0% used"
  printf 'Usage\n\n  %s\n  Current week (all models): %s%% used · resets Oct 23, 4:59pm (Europe/Prague)\n' \
    "$sess" "$2" >"$T/fake/usage.txt"
}

# codex_usage <resets_at primárního okna>
codex_usage() {
  local f=$T/codex/2026/10/21/rollout-2026-10-21T22-00-00-test.jsonl
  {
    echo '{"timestamp":"2026-10-21T20:00:00Z","type":"session_meta","payload":{"cwd":"/x"}}'
    echo '{"timestamp":"2026-10-21T20:01:00Z","type":"event_msg","payload":{"type":"token_count","rate_limits":{"limit_id":"codex","primary":{"used_percent":10.0,"window_minutes":300,"resets_at":1},"secondary":{"used_percent":5.0,"window_minutes":10080,"resets_at":2}}}}'
    echo "{\"timestamp\":\"2026-10-21T20:02:00Z\",\"type\":\"event_msg\",\"payload\":{\"type\":\"token_count\",\"rate_limits\":{\"limit_id\":\"codex\",\"limit_name\":null,\"primary\":{\"used_percent\":24.0,\"window_minutes\":300,\"resets_at\":$1},\"secondary\":{\"used_percent\":8.0,\"window_minutes\":10080,\"resets_at\":1793200936},\"credits\":null}}}"
  } >"$f"
}

pm_output() { printf '%s\n' "$1" >"$T/fake/pm.out"; echo "$2" >"$T/fake/pm.rc"; }

# tick <TICK_NOW> [argumenty]: spustí tick.sh, výstup do $T/out, kód do RC.
tick() {
  local now=$1; shift
  TICK_CONF=$T/tick.conf TICK_NOW=$now PATH="$T/bin:$PATH" bash "$TICK" "$@" >"$T/out" 2>&1
  RC=$?
}

logfile() { cat "$T"/proj/ops/logs/tick-*.log 2>/dev/null; }
calls() { cat "$T/fake/calls.log"; }
count_pm() { grep -c '^claude \[-p\] \[--model\]' "$T/fake/calls.log"; }
count_usage() { grep -c '^claude \[-p\] \[/usage\]' "$T/fake/calls.log"; }
count_codex() { grep -c '^codex ' "$T/fake/calls.log"; }

begin() { CUR=$1; CUR_FAILED=0; setup; }
check() {   # check "popis" příkaz...
  local what=$1; shift
  if ! "$@"; then
    CUR_FAILED=1
    echo "    ✗ $what"
  fi
}
end() {
  if (( CUR_FAILED )); then
    FAIL=$((FAIL + 1))
    echo "FAIL $CUR"
    echo "    --- výstup tick.sh ---"; sed 's/^/    | /' "$T/out"
    echo "    --- volání ---"; calls | sed 's/^/    | /'
  else
    PASS=$((PASS + 1))
    echo "ok   $CUR"
  fi
  teardown
}

has() { grep -qF -- "$2" <<<"$1"; }
eq() { [[ $1 == "$2" ]]; }

# --- Testy --------------------------------------------------------------------

begin "mimo okno (14:00, bez povolení): nic nespustí, ani nečte usage"
tick 2026-10-21T14:00
check "kód 0" eq "$RC" 0
check "žádné volání" eq "$(calls)" ""
check "log říká proč" has "$(logfile)" "nic nespuštěno: mimo noční okno"
end

begin "hranice okna: 20:59 nic, 21:00 PM"
tick 2026-10-21T20:59
check "20:59 nic" eq "$(count_usage)" 0
tick 2026-10-21T21:00
check "21:00 PM spuštěn" eq "$(count_pm)" 1
end

begin "hranice okna: 6:30 ještě spustí, 6:31 už ne"
claude_usage "Oct 22, 7:00am (Europe/Prague)" 70
tick 2026-10-22T06:31
check "6:31 nic" eq "$(count_usage)" 0
tick 2026-10-22T06:30
check "6:30 PM spuštěn" eq "$(count_pm)" 1
check "timeout zkrácen do 7:00 (1800 s)" has "$(logfile)" "timeout 1800 s"
end

begin "povolení-den platné: přes den spustí PM"
echo "do=2026-10-21T15:00" >"$T/proj/ops/povoleni-den"
claude_usage "Oct 21, 6:00pm (Europe/Prague)" 70
tick 2026-10-21T14:00
check "PM spuštěn" eq "$(count_pm)" 1
check "log zmiňuje povolení" has "$(logfile)" "povolení-den platí do 21.10. 15:00"
check "timeout zkrácen do konce povolení (3600 s)" has "$(logfile)" "timeout 3600 s"
end

begin "povolení-den prošlé: jako by nebylo"
echo "do=2026-10-21T12:00" >"$T/proj/ops/povoleni-den"
tick 2026-10-21T14:00
check "nic nespuštěno" eq "$(calls)" ""
check "log říká prošlo" has "$(logfile)" "povolení-den prošlo"
end

begin "povolení-den neplatný formát: jako by nebylo"
echo "do=zítra" >"$T/proj/ops/povoleni-den"
tick 2026-10-21T14:00
check "nic nespuštěno" eq "$(calls)" ""
check "log říká neplatný formát" has "$(logfile)" "neplatný formát"
end

begin "zámek: druhá instance hned skončí se zápisem do logu"
pm_output "hotovo" 0
echo 3 >"$T/fake/pm.sleep"
TICK_CONF=$T/tick.conf TICK_NOW=2026-10-21T23:00 PATH="$T/bin:$PATH" bash "$TICK" >"$T/out1" 2>&1 &
first=$!
for _ in $(seq 50); do (( $(count_pm) > 0 )) && break; sleep 0.1; done
start=$(date +%s)
tick 2026-10-21T23:00
dur=$(( $(date +%s) - start ))
wait "$first"
check "druhá instance kód 0" eq "$RC" 0
check "druhá instance skončila hned (do 2 s)" test "$dur" -le 2
check "PM spuštěn jen jednou" eq "$(count_pm)" 1
check "log o zámku" has "$(logfile)" "jiná instance tick.sh ještě běží"
end

begin "brána Claude pustí: PM se správným příkazem, v KÓD/, výstup v logu"
pm_output "PM: noc zvládnuta" 0
tick 2026-10-21T23:00
check "kód 0" eq "$RC" 0
check "PM spuštěn jednou" eq "$(count_pm)" 1
check "přesné argumenty PM" has "$(calls)" \
  "claude [-p] [--model] [claude-opus-5-5] [--append-system-prompt-file] [$T/kb/vyvoj/prompt-projektovy-manazer.md] [NOČNÍ BĚH 2026-10-21 23:00]"
check "PM běží v PROJECT_DIR se stdin /dev/null" has "$(calls)" "claude-cwd=$T/proj stdin=/dev/null"
check "řádek brány v logu" has "$(logfile)" "BRÁNA Claude: PUSTÍ · 5h okno běží (využito 18 %), obnova 22.10. 02:09 nejpozději v 07:30 · týden zbývá 30 % (min. 20 %)"
check "výstup PM v logu" has "$(logfile)" "PM: noc zvládnuta"
check "PM v pořádku, Codex ne" eq "$(count_codex)" 0
end

begin "CLAUDE_EXTRA_ARGS z konfigurace se předají"
conf 'CLAUDE_EXTRA_ARGS=(--permission-mode=acceptEdits --verbose)'
tick 2026-10-21T23:00
check "argumenty předány" has "$(calls)" "[--permission-mode=acceptEdits] [--verbose] [NOČNÍ BĚH 2026-10-21 23:00]"
end

begin "brána Claude nepustí: pozdní obnova (8:15); koder=A, nic nespustí"
stav "aktivni=F0-B03 koder=A"
claude_usage "Oct 22, 8:15am (Europe/Prague)" 70
tick 2026-10-22T03:15
check "PM nespuštěn" eq "$(count_pm)" 0
check "Codex nespuštěn" eq "$(count_codex)" 0
check "důvod v logu" has "$(logfile)" "BRÁNA Claude: NEPUSTÍ · 5h okno běží (využito 18 %), obnova 22.10. 08:15 je po 07:30"
check "důvod pro Codex v logu" has "$(logfile)" "aktivní blok F0-B03 má kodéra A, ne B"
end

begin "brána Claude nepustí: týden pod limitem (zbývá 15 %)"
stav "aktivni=- koder=-"
claude_usage "Oct 22, 2:09am (Europe/Prague)" 85
tick 2026-10-21T23:00
check "PM nespuštěn" eq "$(count_pm)" 0
check "důvod v logu" has "$(logfile)" "týden zbývá 15 % (min. 20 %): málo"
check "bez aktivního bloku nic" has "$(logfile)" "žádný aktivní blok"
end

begin "MIN_CLAUDE_WEEK_LEFT=0 z konfigurace (výjimka P-006): pustí i při 95 %"
conf "MIN_CLAUDE_WEEK_LEFT=0"
claude_usage "Oct 22, 2:09am (Europe/Prague)" 95
tick 2026-10-21T23:00
check "PM spuštěn" eq "$(count_pm)" 1
check "log" has "$(logfile)" "týden zbývá 5 % (min. 0 %)"
end

begin "okno neběží: ve 2:30 ještě otevře nové (obnova 7:30)"
claude_usage - 70
tick 2026-10-22T02:30
check "PM spuštěn" eq "$(count_pm)" 1
check "log" has "$(logfile)" "5h okno neběží, nové se obnoví 22.10. 07:30 nejpozději v 07:30"
end

begin "okno neběží po 2:30: nepustí"
stav "aktivni=F0-B03 koder=A"
claude_usage - 70
tick 2026-10-22T02:31
check "PM nespuštěn" eq "$(count_pm)" 0
check "log" has "$(logfile)" "5h okno neběží a nové by se obnovilo 22.10. 07:31, tedy po 07:30 (nové okno jen do 22.10. 02:30)"
end

begin "obnova v minulosti = okno neběží (ve 23:00 otevře nové)"
claude_usage "Oct 21, 10:00pm (Europe/Prague)" 70
tick 2026-10-21T23:00
check "PM spuštěn" eq "$(count_pm)" 1
check "log" has "$(logfile)" "5h okno neběží, nové se obnoví 22.10. 04:00"
end

begin "obnova jen časem bez data (resets 2am)"
claude_usage "2am (Europe/Prague)" 70
tick 2026-10-21T23:00
check "PM spuštěn" eq "$(count_pm)" 1
check "log" has "$(logfile)" "obnova 22.10. 02:00"
end

begin "limit PM → záložní Codex v tomtéž běhu, se zadáním a P-005"
pm_output $'pracuji...\nClaude AI usage limit reached|1792640000' 1
tick 2026-10-21T23:00
check "PM spuštěn" eq "$(count_pm)" 1
check "limit v logu" has "$(logfile)" "PM skončil kvůli LIMITU (kód 1)"
check "brána Codex v logu" has "$(logfile)" "BRÁNA Codex: PUSTÍ · 5h okno běží (využito 24 %), obnova 22.10. 00:30 nejpozději v 07:30 · týden využito 8 % (pravidlo neplatí)"
check "Codex spuštěn jednou" eq "$(count_codex)" 1
check "argumenty Codexu" has "$(calls)" \
  "codex [exec] [-m] [gpt-6-astra] [-c] [model_reasoning_effort=\"high\"] [-s] [workspace-write] [--skip-git-repo-check] [-C] [$T/wt/F0-B03] [--add-dir] [$T/kb] [-c] [sandbox_workspace_write.network_access=true] [-c] [sandbox_workspace_write.writable_roots=[\"/home/honza/jr-content/.git\"]] [-o] [$T/proj/ops/logs/F0-B03-B-posledni.txt] [Pracuješ na bloku F0-B03 projektu JR Content ve worktree $T/wt/F0-B03."
check "zadání: jen dokončovat, nemergovat" has "$(calls)" "Smíš jen dokončovat tento aktivní blok. Nesmíš plánovat, zadávat nové bloky, mergovat ani měnit plán"
check "Codex běží ve worktree se stdin /dev/null" has "$(calls)" "codex-cwd=$T/wt/F0-B03 stdin=/dev/null"
check "výstup Codexu v logu bloku" has "$(cat "$T/proj/ops/logs/F0-B03-B.log")" "falešný codex pracuje"
end

begin "limit PM rozpoznaný podle návratového kódu z konfigurace"
conf 'CLAUDE_LIMIT_EXIT_CODES="42"'
pm_output "něco se pokazilo" 42
tick 2026-10-21T23:00
check "limit v logu" has "$(logfile)" "PM skončil kvůli LIMITU (kód 42)"
check "Codex spuštěn" eq "$(count_codex)" 1
end

begin "PM skončí chybou bez limitu: Codex se nespustí"
pm_output "Error: něco jiného" 1
tick 2026-10-21T23:00
check "log" has "$(logfile)" "PM skončil chybou (kód 1), limit nerozpoznán"
check "Codex nespuštěn" eq "$(count_codex)" 0
end

begin "PM vypršel čas (kód 124): Codex se nespustí"
pm_output "" 124
tick 2026-10-21T23:00
check "log" has "$(logfile)" "PM ukončen po vypršení času"
check "Codex nespuštěn" eq "$(count_codex)" 0
end

begin "limit PM, ale koder=A: záložní Codex nespustí"
stav "aktivni=F0-B03 koder=A"
pm_output "You've hit your limit · resets 2am (Europe/Prague)" 1
tick 2026-10-21T23:00
check "limit v logu" has "$(logfile)" "LIMITU"
check "Codex nespuštěn" eq "$(count_codex)" 0
check "důvod" has "$(logfile)" "má kodéra A, ne B"
end

begin "brána Claude nepustí → Codex rovnou (koder=B)"
claude_usage "Oct 22, 9:00am (Europe/Prague)" 70
codex_usage "$(epoch '2026-10-22 06:00')"
tick 2026-10-22T04:00
check "PM nespuštěn" eq "$(count_pm)" 0
check "Codex spuštěn" eq "$(count_codex)" 1
check "důvod v logu" has "$(logfile)" "důvod: brána Claude nepustila"
end

begin "brána Codex nepustí: obnova po 7:30"
claude_usage "Oct 22, 9:00am (Europe/Prague)" 70
codex_usage "$(epoch '2026-10-22 08:00')"
tick 2026-10-22T04:00
check "Codex nespuštěn" eq "$(count_codex)" 0
check "log" has "$(logfile)" "BRÁNA Codex: NEPUSTÍ · 5h okno běží (využito 24 %), obnova 22.10. 08:00 je po 07:30"
end

begin "chybějící data usage Claude: nepustí, přejde na Codex"
rm -f "$T/fake/usage.txt"; echo 1 >"$T/fake/usage.rc"
tick 2026-10-21T23:00
check "PM nespuštěn" eq "$(count_pm)" 0
check "log" has "$(logfile)" "BRÁNA Claude: NEPUSTÍ · chybí data usage (řádek Current week), claude /usage skončil kódem 1"
check "Codex spuštěn" eq "$(count_codex)" 1
end

begin "chybějící data usage Codexu: okno neběží (23:00 pustí, 3:00 ne)"
stav "aktivni=F0-B03 koder=B"
rm -rf "$T/codex"
claude_usage "Oct 22, 9:00am (Europe/Prague)" 70
tick 2026-10-21T23:00
check "23:00 Codex spuštěn" eq "$(count_codex)" 1
check "log 23:00" has "$(logfile)" "5h okno neběží, nové se obnoví 22.10. 04:00 nejpozději v 07:30 · data usage Codexu nenalezena"
tick 2026-10-22T03:00
check "3:00 Codex už nespuštěn" eq "$(count_codex)" 1
end

begin "stav.md bez řádku tick: Codex nespustí"
echo "# STAV bez řádku" >"$T/kb/vyvoj/stav.md"
claude_usage "Oct 22, 9:00am (Europe/Prague)" 70
tick 2026-10-22T04:00
check "Codex nespuštěn" eq "$(count_codex)" 0
check "log" has "$(logfile)" "chybí řádek <!-- tick:"
end

begin "na bloku už běží codex exec: druhý nespustí"
claude_usage "Oct 22, 9:00am (Europe/Prague)" 70
codex_usage "$(epoch '2026-10-22 06:00')"
bash -c 'sleep 20; true' codex exec -C "$T/wt/F0-B03" &
busy=$!
tick 2026-10-22T04:00
kill "$busy" 2>/dev/null; wait "$busy" 2>/dev/null
check "Codex nespuštěn" eq "$(count_codex)" 0
check "log" has "$(logfile)" "na F0-B03 už běží codex exec (PID $busy)"
end

begin "ANTHROPIC_API_KEY v prostředí: odmítne běžet"
ANTHROPIC_API_KEY=x tick 2026-10-21T23:00
check "kód 3" eq "$RC" 3
check "nic nevolá" eq "$(calls)" ""
check "log" has "$(logfile)" "ODMÍTÁM BĚŽET"
end

begin "OPENAI_API_KEY (i prázdné) v prostředí: odmítne běžet"
OPENAI_API_KEY= tick 2026-10-21T23:00
check "kód 3" eq "$RC" 3
check "nic nevolá" eq "$(calls)" ""
end

begin "--dry-run: čte usage, vypíše rozhodnutí, nic nespustí, log nepíše"
tick 2026-10-21T23:00 --dry-run
check "kód 0" eq "$RC" 0
check "usage přečtena" eq "$(count_usage)" 1
check "PM nespuštěn" eq "$(count_pm)" 0
check "Codex nespuštěn" eq "$(count_codex)" 0
check "žádný log" eq "$(logfile)" ""
check "vypíše PM" has "$(cat "$T/out")" "SPUSTIL BYCH PM"
check "vypíše záložní plán" has "$(cat "$T/out")" "kdyby PM narazil na limit: SPUSTIL BYCH záložní Codex na F0-B03"
check "vypíše surovou usage" has "$(cat "$T/out")" "Current week (all models): 70% used"
end

begin "--dry-run mimo okno: jen vypíše důvod"
tick 2026-10-21T12:00 --dry-run
check "nic nevolá" eq "$(calls)" ""
check "vypíše důvod" has "$(cat "$T/out")" "nic nespuštěno: mimo noční okno"
end

begin "skutečná ops/tick.conf: výchozí hodnoty a žádné tajné údaje"
(
  source "$REPO/ops/tick.conf"
  [[ $MIN_CLAUDE_WEEK_LEFT == 20 && $WINDOW_START == 21:00 && $WINDOW_END == 07:00 \
     && $LAST_START == 06:30 && $RESET_DEADLINE == 07:30 && $PM_MODEL == claude-opus-5-5 \
     && $PROJECT_DIR == /home/honza/jr-content && $CODEX_MODEL == gpt-6-astra \
     && " ${CODEX_EXTRA_ARGS[*]} " == *" sandbox_workspace_write.network_access=true "* ]]
) >"$T/out" 2>&1
check "výchozí hodnoty" eq "$?" 0
check "žádné klíče ani hesla" eq "$(grep -Eci '(api_key|token|password|heslo|secret)[[:space:]]*=' "$REPO/ops/tick.conf")" 0
end

echo
echo "Výsledek: prošlo $PASS, selhalo $FAIL"
(( FAIL == 0 ))
