#!/usr/bin/env bash
# PM: záloha → merge blok/<ID> (argument) → přestavba obrazů a restart hlavního jrc → celá sada testů. Hlídač stahování 3 GB.
cd /home/honza/jr-content; export JRC_PROJECT=jrc
dev=$(ip route | awk '/default/{print $5; exit}'); s=$(cat /sys/class/net/$dev/statistics/rx_bytes)
mb() { echo $(( ($(cat /sys/class/net/$dev/statistics/rx_bytes)-s)/1000000 )); }
make backup && b=$(ls -1d backups/jrc/2* | tail -1) && make backup-verify BACKUP="$b" && echo "ZÁLOHA OK $b" || { echo "ZÁLOHA SELHALA"; exit 1; }
br=${1:?blok}; git merge-base --is-ancestor blok/$br HEAD || git merge -q --no-ff blok/$br -m "$br: merge

Agent: PM (Claude Opus 5.5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013GSXPUXGdTaPiq8T6s8zrT" || { echo "MERGE SELHAL"; exit 1; }
echo "MERGE $(git log --oneline -1 | cut -c1-8)"
( while sleep 20; do [ $(mb) -gt 3000 ] && { echo "LIMIT STAHOVÁNÍ $(mb) MB → zastavuji sestavení"; pkill -f "docker-buildx bake"; pkill -f "make up"; break; }; done ) & w=$!
echo "$(date '+%H:%M') make up"; make up; rc=$?; echo "make up kód $rc, staženo $(mb) MB"
[ $rc = 0 ] || { kill $w; exit 1; }
# Služby s kódem připojeným ze složky (BB, FF API/worker/beat/maintenance) se při nezměněném obrazu nepřevytvoří –
# bez restartu by běžel starý kód (7. 11.: FF API po P1-B26). Restartuj všechny kontejnery projektu, které mají bind mount repozitáře.
for c in $(docker ps --filter "label=com.docker.compose.project=jrc" --format '{{.Names}}'); do
  docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$c" | grep -qE '/home/honza/jr-content/(brightbean|freeframe)' && docker restart "$c" >/dev/null && echo "restart $c"
done; sleep 20
echo "$(date '+%H:%M') make test"; make test; echo "make test kód $?, staženo celkem $(mb) MB"; kill $w 2>/dev/null
git push -q origin main; echo "$(date '+%H:%M') HOTOVO"
