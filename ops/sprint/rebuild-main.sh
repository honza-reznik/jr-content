#!/usr/bin/env bash
# PM: záloha → merge P1-B10 → přestavba obrazů a restart hlavního jrc → celá sada testů. Hlídač stahování 3 GB.
cd /home/honza/jr-content; export JRC_PROJECT=jrc
dev=$(ip route | awk '/default/{print $5; exit}'); s=$(cat /sys/class/net/$dev/statistics/rx_bytes)
mb() { echo $(( ($(cat /sys/class/net/$dev/statistics/rx_bytes)-s)/1000000 )); }
make backup && b=$(ls -1d backups/jrc/2* | tail -1) && make backup-verify BACKUP="$b" && echo "ZÁLOHA OK $b" || { echo "ZÁLOHA SELHALA"; exit 1; }
git merge -q --no-ff blok/P1-B10 -m "P1-B10: merge

Agent: PM (Claude Opus 5.5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013GSXPUXGdTaPiq8T6s8zrT" || { echo "MERGE SELHAL"; exit 1; }
echo "MERGE $(git log --oneline -1 | cut -c1-8)"
( while sleep 20; do [ $(mb) -gt 3000 ] && { echo "LIMIT STAHOVÁNÍ $(mb) MB → zastavuji sestavení"; pkill -f "docker-buildx bake"; pkill -f "make up"; break; }; done ) & w=$!
echo "$(date '+%H:%M') make up"; make up; rc=$?; echo "make up kód $rc, staženo $(mb) MB"
[ $rc = 0 ] || { kill $w; exit 1; }
echo "$(date '+%H:%M') make test"; make test; echo "make test kód $?, staženo celkem $(mb) MB"; kill $w 2>/dev/null
git push -q origin main; echo "$(date '+%H:%M') HOTOVO"
