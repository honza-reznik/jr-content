#!/usr/bin/env bash
# ops/tick.sh: noční spouštěč JR Content (blok F0-B05).
#
# Plánovač (F0-B06) ho pouští každých 30 minut. Skript podle času, denního povolení a limitní
# brány (plán 4.4) buď spustí PM (Claude), záložní Codex na aktivním bloku kodéra B, nebo jen
# zapíše do logu, proč nic nespustil. Návod: KB/aplikace/provoz/tick.md.
#
# Použití:  ops/tick.sh [--dry-run]
#   --dry-run  jen vypíše rozhodnutí (usage čte naostro), nic nespustí, nezapisuje log, nebere zámek.
#
# Proměnné prostředí (hlavně pro testy):
#   TICK_CONF  cesta ke konfiguraci (výchozí ops/tick.conf vedle skriptu)
#   TICK_NOW   podvržený „teď“ ve tvaru AAAA-MM-DDTHH:MM (místní čas)
#   TICK_NOW_AFTER_PM  podvržený „teď“ po doběhnutí PM (jen testy; jinak se čas znovu načte z hodin)

set -uo pipefail

usage() { sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

DRY_RUN=0
for arg in "$@"; do
  case $arg in
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "tick: neznámý argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONF=${TICK_CONF:-$SCRIPT_DIR/tick.conf}
if [[ ! -r $CONF ]]; then
  echo "tick: nelze číst konfiguraci $CONF" >&2
  exit 2
fi
# shellcheck source=tick.conf
source "$CONF"

for var in PROJECT_DIR KB_DIR WT_ROOT STAV_FILE PM_PROMPT_FILE PERMIT_FILE LOCK_FILE LOG_DIR \
           CODEX_SESSIONS_DIR WINDOW_START WINDOW_END LAST_START RESET_DEADLINE NEW_WINDOW_HOURS \
           MIN_CLAUDE_WEEK_LEFT CLAUDE_BIN PM_MODEL PM_TIMEOUT USAGE_TIMEOUT CLAUDE_LIMIT_PATTERNS \
           LIMIT_TAIL_LINES CODEX_BIN CODEX_MODEL CODEX_REASONING CODEX_TIMEOUT CODEX_TASK; do
  if [[ -z ${!var:-} ]]; then
    echo "tick: v konfiguraci $CONF chybí $var" >&2
    exit 2
  fi
done
declare -p CLAUDE_EXTRA_ARGS >/dev/null 2>&1 || CLAUDE_EXTRA_ARGS=()
declare -p CODEX_EXTRA_ARGS >/dev/null 2>&1 || CODEX_EXTRA_ARGS=()
CLAUDE_LIMIT_EXIT_CODES=${CLAUDE_LIMIT_EXIT_CODES:-}

# --- Čas ---------------------------------------------------------------------

now_epoch() {   # $1 = podvržený čas (AAAA-MM-DDTHH:MM) nebo prázdné = skutečné hodiny
  if [[ -n $1 ]]; then
    date -d "$1" +%s || { echo "tick: neplatný podvržený čas: $1" >&2; return 1; }
  else
    date +%s
  fi
}
NOW=$(now_epoch "${TICK_NOW:-}") || exit 2
LOGFILE=$LOG_DIR/tick-$(date -d "@$NOW" +%F).log

# Volitelné absolutní hranice; chybějící hodnoty zachovávají staré konfigurace.
for var in CONTINUOUS_UNTIL CLAUDE_FREE_BY CODEX_FREE_BY WEEK_RULE_FROM; do
  val=${!var:-}
  epoch=0
  if [[ -n $val ]]; then
    if [[ ! $val =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$ ]] \
       || ! epoch=$(date -d "$val" +%s 2>/dev/null); then
      echo "tick: neplatný $var=$val (čekám AAAA-MM-DDTHH:MM)" >&2
      exit 2
    fi
  fi
  printf -v "${var}_EPOCH" '%s' "$epoch"
done

fmt() { date -d "@$1" '+%-d.%-m. %H:%M'; }

log() {
  local line t=$NOW
  [[ -z ${TICK_NOW:-} ]] && t=$(date +%s)   # skutečný čas zápisu; v testech podvržený
  line="$(date -d "@$t" '+%F %H:%M') $*"
  if (( DRY_RUN )); then
    echo "[nanečisto] $line"
  else
    echo "$line"
    echo "$line" >>"$LOGFILE"
  fi
}

# --- Pomocník v Pythonu: usage a výpočty s časem --------------------------------
# Jen standardní knihovna. Výstup brány: první řádek OK/NE, druhý řádek čitelné zdůvodnění.

read -r -d '' PY_HELPER <<'PY'
import datetime as dt, json, os, re, sys, time

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

def local_epoch(y, mo, d, h, mi, tzname=None):
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return int(dt.datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tzname)).timestamp())
        except Exception:
            pass
    return int(time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)))

def fmt(e):
    t = time.localtime(e)
    return f"{t.tm_mday}.{t.tm_mon}. {t.tm_hour:02d}:{t.tm_min:02d}"

def next_at(now, hm):
    """Nejbližší čas HH:MM (místní) v okamžiku now nebo po něm."""
    h, m = (int(x) for x in hm.split(":"))
    base = dt.date.fromtimestamp(now)
    for add in range(3):
        d = base + dt.timedelta(days=add)
        e = local_epoch(d.year, d.month, d.day, h, m)
        if e >= now:
            return e
    raise SystemExit("next_at selhalo")

def parse_reset(s, now):
    """'Oct 22, 2:09am (Europe/Prague)' nebo '2am (Europe/Prague)' -> unixový čas, jinak None."""
    tz = None
    mt = re.search(r"\(([^)]+)\)", s)
    if mt:
        tz = mt.group(1).strip()
        s = s[:mt.start()]
    s = re.sub(r"\s+", " ", s.replace(",", " ").replace(" at ", " ")).strip()
    m = re.match(r"^(?:([A-Za-z]{3})[A-Za-z]*\.? (\d{1,2}) )?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?$", s, re.I)
    if not m:
        return None
    mon, day, hh, mm, ampm = m.groups()
    h, mi = int(hh), int(mm or 0)
    if ampm:
        h = h % 12 + (12 if ampm.lower() == "pm" else 0)
    if h > 23 or mi > 59:
        return None
    today = dt.date.fromtimestamp(now)
    try:
        if mon:
            mo = MONTHS.get(mon.lower()[:3])
            if not mo:
                return None
            e = local_epoch(today.year, mo, int(day), h, mi, tz)
            if e < now - 180 * 86400:      # přelom roku: "Jan 1" v prosinci
                e = local_epoch(today.year + 1, mo, int(day), h, mi, tz)
        else:
            e = local_epoch(today.year, today.month, today.day, h, mi, tz)
            if e < now:
                e += 86400
    except ValueError:
        return None
    return e

def num(x):
    return f"{x:g}"

def window_verdict(name, now, used, reset, deadline_hm, new_hours, continuous_until, free_by):
    """Pravidlo 5h okna. Vrací (ok, text)."""
    continuous = now < continuous_until
    deadline = None if continuous else next_at(now, deadline_hm)
    deadline_txt = deadline_hm
    if now < free_by and (deadline is None or free_by <= deadline):
        deadline = free_by
        deadline_txt = f"{name.upper()}_FREE_BY {fmt(free_by)}"
    extra = " · nepřetržitý provoz, RESET_DEADLINE neplatí" if continuous else ""
    used_txt = f" (využito {num(used)} %)" if used is not None else ""
    if reset is not None and reset > now:
        if deadline is None:
            return True, f"5h okno běží{used_txt}, obnova {fmt(reset)}{extra}"
        if reset <= deadline:
            return True, f"5h okno běží{used_txt}, obnova {fmt(reset)} nejpozději v {deadline_txt}{extra}"
        return False, f"5h okno běží{used_txt}, obnova {fmt(reset)} je po {deadline_txt}{extra}"
    new_reset = now + int(float(new_hours) * 3600)
    if deadline is None:
        return True, f"5h okno neběží, nové se obnoví {fmt(new_reset)}{extra}"
    if new_reset <= deadline:
        return True, f"5h okno neběží, nové se obnoví {fmt(new_reset)} nejpozději v {deadline_txt}{extra}"
    return False, (f"5h okno neběží a nové by se obnovilo {fmt(new_reset)}, tedy po {deadline_txt}"
                   f" (nové okno jen do {fmt(deadline - int(float(new_hours) * 3600))}){extra}")

def gate_claude(now, deadline_hm, new_hours, min_left, rc, continuous_until, free_by, week_rule_from):
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", sys.stdin.read())
    used = reset = week = None
    m = re.search(r"Current session:\s*([\d.]+)\s*%\s*used([^\n]*)", text)
    if m:
        used = float(m.group(1))
        r = re.search(r"resets?\s+(.+)", m.group(2))
        if r:
            reset = parse_reset(r.group(1), now)
    m = re.search(r"Current week \(all models\):\s*([\d.]+)\s*%\s*used", text)
    if m:
        week = float(m.group(1))
    if week is None:
        why = "chybí data usage (řádek Current week)"
        if rc != "0":
            why += f", claude /usage skončil kódem {rc}"
        return False, why
    ok, txt = window_verdict("Claude", now, used, reset, deadline_hm, new_hours, continuous_until, free_by)
    week_note = ""
    if now < week_rule_from:
        min_left = 0
        week_note = f" · před WEEK_RULE_FROM {fmt(week_rule_from)} platí minimum 0 %"
    left = 100 - week
    wtxt = f"týden zbývá {num(left)} % (min. {num(min_left)} %)"
    if left < min_left:
        return False, f"{txt} · {wtxt}: málo{week_note}"
    return ok, f"{txt} · {wtxt}{week_note}"

def find_key(o, key):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = find_key(v, key)
            if r is not None:
                return r
    return None

def codex_limits(sessions_dir):
    """Poslední rate_limits z nejnovějšího souboru relace, který je obsahuje."""
    files = []
    for root, _, names in os.walk(sessions_dir):
        for n in names:
            if n.endswith(".jsonl"):
                p = os.path.join(root, n)
                try:
                    files.append((os.path.getmtime(p), p))
                except OSError:
                    pass
    for _, p in sorted(files, reverse=True)[:10]:
        last = None
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        rl = find_key(json.loads(line), "rate_limits")
                    except ValueError:
                        continue
                    if isinstance(rl, dict) and isinstance(rl.get("primary"), dict):
                        last = rl
        except OSError:
            continue
        if last:
            return last, p
    return None, None

def gate_codex(now, deadline_hm, new_hours, sessions_dir, continuous_until, free_by):
    rl, path = codex_limits(sessions_dir)
    used = reset = None
    extra = ""
    if rl:
        p = rl["primary"]
        try:
            used = float(p.get("used_percent"))
        except (TypeError, ValueError):
            pass
        try:
            reset = int(p.get("resets_at"))
        except (TypeError, ValueError):
            pass
        sec = rl.get("secondary") or {}
        if isinstance(sec, dict) and sec.get("used_percent") is not None:
            extra = f" · týden využito {num(float(sec['used_percent']))} % (pravidlo neplatí)"
    else:
        extra = " · data usage Codexu nenalezena, beru jako okno neběží"
    ok, txt = window_verdict("Codex", now, used, reset, deadline_hm, new_hours, continuous_until, free_by)
    return ok, txt + extra

cmd = sys.argv[1]
if cmd == "next_at":
    print(next_at(int(sys.argv[2]), sys.argv[3]))
elif cmd == "gate_claude":
    ok, txt = gate_claude(int(sys.argv[2]), sys.argv[3], sys.argv[4], float(sys.argv[5]), sys.argv[6],
                          int(sys.argv[7]), int(sys.argv[8]), int(sys.argv[9]))
    print("OK" if ok else "NE"); print(txt)
elif cmd == "gate_codex":
    ok, txt = gate_codex(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5],
                         int(sys.argv[6]), int(sys.argv[7]))
    print("OK" if ok else "NE"); print(txt)
else:
    raise SystemExit(f"neznámý příkaz {cmd}")
PY

py() { python3 -I -c "$PY_HELPER" "$@"; }

# --- Časové okno a denní povolení ----------------------------------------------

hm_to_min() { local h=${1%%:*} m=${1##*:}; echo $(( 10#$h * 60 + 10#$m )); }

PERMIT_UNTIL=""   # unixový čas konce platného denního povolení, jinak prázdné
IN_NIGHT=0        # 1 = teď je noční okno pro start (WINDOW_START až LAST_START včetně)
TIME_TEXT=""

check_time() {
  if (( NOW < CONTINUOUS_UNTIL_EPOCH )); then
    IN_NIGHT=0
    PERMIT_UNTIL=""
    TIME_TEXT="nepřetržitý provoz do $(fmt "$CONTINUOUS_UNTIL_EPOCH") (CONTINUOUS_UNTIL)"
    return 0
  fi
  local cur start last
  cur=$(hm_to_min "$(date -d "@$NOW" +%H:%M)")
  start=$(hm_to_min "$WINDOW_START")
  last=$(hm_to_min "$LAST_START")
  IN_NIGHT=0
  if (( start <= last )); then
    (( cur >= start && cur <= last )) && IN_NIGHT=1
  else
    (( cur >= start || cur <= last )) && IN_NIGHT=1
  fi

  PERMIT_UNTIL=""
  local permit_txt="povolení-den není"
  if [[ -f $PERMIT_FILE ]]; then
    local val until
    val=$(grep -m1 -E '^[[:space:]]*do=' "$PERMIT_FILE" | sed -E 's/^[[:space:]]*do=//; s/[[:space:]]+$//')
    if [[ $val =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$ ]] && until=$(date -d "$val" +%s 2>/dev/null); then
      if (( until > NOW )); then
        PERMIT_UNTIL=$until
        permit_txt="povolení-den platí do $(fmt "$until")"
      else
        permit_txt="povolení-den prošlo $(fmt "$until")"
      fi
    else
      permit_txt="povolení-den má neplatný formát (čekám do=AAAA-MM-DDTHH:MM)"
    fi
  fi

  if (( IN_NIGHT )); then
    TIME_TEXT="v nočním okně $WINDOW_START–$LAST_START · $permit_txt"
    return 0
  elif [[ -n $PERMIT_UNTIL ]]; then
    TIME_TEXT="mimo noční okno, ale $permit_txt"
    return 0
  fi
  TIME_TEXT="mimo noční okno $WINDOW_START–$LAST_START · $permit_txt"
  return 1
}

# Timeout běhu: z konfigurace, nejdéle do konce nepřetržitého režimu, jinak noci/povolení.
effective_timeout() {
  local conf_to=$1 end=0 e
  if (( NOW < CONTINUOUS_UNTIL_EPOCH )); then
    e=$(( CONTINUOUS_UNTIL_EPOCH - NOW ))
    (( e < conf_to )) && conf_to=$e
    echo "$conf_to"
    return
  fi
  if (( IN_NIGHT )); then
    end=$(py next_at "$NOW" "$WINDOW_END")
  fi
  if [[ -n $PERMIT_UNTIL ]] && (( PERMIT_UNTIL > end )); then
    end=$PERMIT_UNTIL
  fi
  local to=$conf_to
  if (( end > 0 )); then
    e=$(( end - NOW ))
    (( e < to )) && to=$e
  fi
  (( to < 60 )) && to=60
  echo "$to"
}

# --- Limitní brány ---------------------------------------------------------------

GATE_TEXT=""

claude_gate() {
  local out rc res
  out=$(cd "$PROJECT_DIR" 2>/dev/null; timeout "$USAGE_TIMEOUT" "$CLAUDE_BIN" -p "/usage" </dev/null 2>&1 9>&-)
  rc=$?
  if (( DRY_RUN )); then
    local raw
    raw=$(printf '%s\n' "$out" | grep -E 'Current (session|week)' | sed 's/^[[:space:]]*/    /')
    echo "[nanečisto] surová usage Claude (kód $rc):"
    echo "${raw:-    (žádné řádky Current session/week)}"
  fi
  res=$(printf '%s' "$out" | py gate_claude "$NOW" "$RESET_DEADLINE" "$NEW_WINDOW_HOURS" "$MIN_CLAUDE_WEEK_LEFT" "$rc" \
    "$CONTINUOUS_UNTIL_EPOCH" "$CLAUDE_FREE_BY_EPOCH" "$WEEK_RULE_FROM_EPOCH")
  GATE_TEXT=$(sed -n 2p <<<"$res")
  [[ $(head -n1 <<<"$res") == OK ]]
}

codex_gate() {
  local res
  res=$(py gate_codex "$NOW" "$RESET_DEADLINE" "$NEW_WINDOW_HOURS" "$CODEX_SESSIONS_DIR" \
    "$CONTINUOUS_UNTIL_EPOCH" "$CODEX_FREE_BY_EPOCH")
  GATE_TEXT=$(sed -n 2p <<<"$res")
  [[ $(head -n1 <<<"$res") == OK ]]
}

# --- Spuštění PM ------------------------------------------------------------------

PM_RESULT=""   # ok | limit | timeout | chyba

run_pm() {
  local msg to out rc
  msg="NOČNÍ BĚH $(date -d "@$NOW" '+%Y-%m-%d %H:%M')"
  local cmd=("$CLAUDE_BIN" -p --model "$PM_MODEL" --append-system-prompt-file "$PM_PROMPT_FILE"
             "${CLAUDE_EXTRA_ARGS[@]}" "$msg")
  to=$(effective_timeout "$PM_TIMEOUT")
  if (( DRY_RUN )); then
    log "SPUSTIL BYCH PM (v $PROJECT_DIR, timeout ${to} s): $(printf '%q ' "${cmd[@]}")"
    return
  fi
  log "SPOUŠTÍM PM (v $PROJECT_DIR, timeout ${to} s): $(printf '%q ' "${cmd[@]}")"
  echo "----- výstup PM: začátek -----" >>"$LOGFILE"
  out=$(mktemp "${TMPDIR:-/tmp}/tick-pm.XXXXXX")
  ( exec 9>&-; cd "$PROJECT_DIR" && exec timeout --kill-after=60 "$to" "${cmd[@]}" ) </dev/null 2>&1 \
    | tee -a "$LOGFILE" >"$out" 9>&-
  rc=${PIPESTATUS[0]}
  echo "----- výstup PM: konec (kód $rc) -----" >>"$LOGFILE"

  local code limit=0
  for code in $CLAUDE_LIMIT_EXIT_CODES; do
    [[ $rc == "$code" ]] && limit=1
  done
  # Kód 0 je vždy úspěch; vzory a kódy limitu se vyhodnocují jen při nenulovém kódu.
  if (( rc == 0 )); then
    PM_RESULT=ok
    log "PM skončil v pořádku."
  elif (( rc == 124 || rc == 137 )); then
    PM_RESULT=timeout
    log "PM ukončen po vypršení času (kód $rc). Záložní Codex se nespouští."
  elif (( limit )) || tail -n "$LIMIT_TAIL_LINES" "$out" | grep -Eqi -- "$CLAUDE_LIMIT_PATTERNS"; then
    PM_RESULT=limit
    log "PM skončil kvůli LIMITU (kód $rc). Přecházím na záložní Codex."
  else
    PM_RESULT=chyba
    log "PM skončil chybou (kód $rc), limit nerozpoznán. Záložní Codex se nespouští."
  fi
  rm -f "$out"
}

# --- Záložní Codex ----------------------------------------------------------------

codex_running_for() {   # vypíše PID běžícího `codex exec` pro daný worktree
  local wt=$1 p c
  for p in /proc/[0-9]*/cmdline; do
    c=$(tr '\0' ' ' <"$p" 2>/dev/null) || continue
    if [[ $c == *codex*" exec "* && $c == *"$wt"* ]]; then
      p=${p#/proc/}; echo "${p%/cmdline}"
      return 0
    fi
  done
  return 1
}

run_codex_fallback() {   # $1 = proč Claude nejde
  local why=$1 pre=""
  (( DRY_RUN )) && [[ $why == hypoteticky ]] && pre="kdyby PM narazil na limit: "

  if ! check_time; then
    log "${pre}záložní Codex nespuštěn: $TIME_TEXT"
    return
  fi
  local line id koder
  line=$(grep -m1 -oE '<!--[[:space:]]*tick:[^>]*-->' "$STAV_FILE" 2>/dev/null)
  if [[ -z $line ]]; then
    log "${pre}záložní Codex nespuštěn: ve $STAV_FILE chybí řádek <!-- tick: aktivni=… koder=… -->"
    return
  fi
  id=$(sed -nE 's/.*aktivni=([^[:space:]>]+).*/\1/p' <<<"$line")
  koder=$(sed -nE 's/.*koder=([^[:space:]>]+).*/\1/p' <<<"$line")
  if [[ -z $id || $id == - ]]; then
    log "${pre}záložní Codex nespuštěn: žádný aktivní blok (aktivni=${id:-?})"
    return
  fi
  if [[ ! $id =~ ^[A-Za-z0-9._-]+$ ]]; then
    log "${pre}záložní Codex nespuštěn: neplatné ID bloku '$id'"
    return
  fi
  if [[ $koder != B ]]; then
    log "${pre}záložní Codex nespuštěn: aktivní blok $id má kodéra ${koder:-?}, ne B"
    return
  fi
  local wt=$WT_ROOT/$id
  if [[ ! -d $wt ]]; then
    log "${pre}záložní Codex nespuštěn: worktree $wt neexistuje"
    return
  fi
  local ok=0
  codex_gate && ok=1
  log "BRÁNA Codex: $( ((ok)) && echo PUSTÍ || echo NEPUSTÍ ) · $GATE_TEXT"
  if (( ! ok )); then
    log "${pre}záložní Codex nespuštěn: brána Codex nepustila"
    return
  fi
  local pid
  if pid=$(codex_running_for "$wt"); then
    log "${pre}záložní Codex nespuštěn: na $id už běží codex exec (PID $pid)"
    return
  fi

  local task=${CODEX_TASK//\{ID\}/$id}
  task=${task//\{WT\}/$wt}
  local to
  to=$(effective_timeout "$CODEX_TIMEOUT")
  local cmd=("$CODEX_BIN" exec -m "$CODEX_MODEL" -c "model_reasoning_effort=\"$CODEX_REASONING\""
             -s workspace-write --skip-git-repo-check -C "$wt" --add-dir "$KB_DIR"
             "${CODEX_EXTRA_ARGS[@]}" -o "$LOG_DIR/$id-B-posledni.txt" "$task")
  local blog=$LOG_DIR/$id-B.log
  if (( DRY_RUN )); then
    log "${pre}SPUSTIL BYCH záložní Codex na $id (v $wt, timeout ${to} s, výstup $blog): $(printf '%q ' "${cmd[@]}")"
    return
  fi
  log "SPOUŠTÍM záložní Codex na $id (důvod: $why; v $wt, timeout ${to} s, výstup $blog): $(printf '%q ' "${cmd[@]}")"
  echo "===== $(date -d "@$NOW" '+%F %H:%M') záložní Codex spuštěn z tick.sh =====" >>"$blog"
  ( exec 9>&-; cd "$wt" && exec timeout --kill-after=60 "$to" "${cmd[@]}" ) </dev/null >>"$blog" 2>&1
  local rc=$?
  log "záložní Codex na $id skončil (kód $rc)"
}

# --- Hlavní běh -------------------------------------------------------------------

main() {
  if [[ -n ${ANTHROPIC_API_KEY+x} || -n ${OPENAI_API_KEY+x} ]]; then
    (( DRY_RUN )) || mkdir -p "$LOG_DIR"
    log "ODMÍTÁM BĚŽET: v prostředí je ANTHROPIC_API_KEY nebo OPENAI_API_KEY (placené API je zakázané)"
    exit 3
  fi

  if (( DRY_RUN )); then
    echo "[nanečisto] nic se nespustí, log se nezapisuje, zámek se nebere"
  else
    mkdir -p "$LOG_DIR"
    exec 9>>"$LOCK_FILE"
    if ! flock -n 9; then
      log "jiná instance tick.sh ještě běží (zámek $LOCK_FILE), končím"
      exit 0
    fi
  fi

  if ! check_time; then
    log "nic nespuštěno: $TIME_TEXT"
    exit 0
  fi
  log "čas: $TIME_TEXT"

  local ok=0
  claude_gate && ok=1
  log "BRÁNA Claude: $( ((ok)) && echo PUSTÍ || echo NEPUSTÍ ) · $GATE_TEXT"

  if (( ok )); then
    run_pm
    if (( DRY_RUN )); then
      run_codex_fallback hypoteticky
    elif [[ $PM_RESULT == limit ]]; then
      # PM mohl běžet hodiny: čas okna, brána Codexu i timeout se počítají znovu od aktuálního času.
      NOW=$(now_epoch "${TICK_NOW_AFTER_PM:-${TICK_NOW:-}}") || exit 2
      run_codex_fallback "PM narazil na limit"
    fi
  else
    run_codex_fallback "brána Claude nepustila"
  fi
  log "tick hotov"
}

main
