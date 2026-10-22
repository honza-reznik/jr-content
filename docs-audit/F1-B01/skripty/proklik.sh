#!/usr/bin/env bash
# Spustí jeden krok proklikání (Playwright) v kontejneru jrc-dev proti auditnímu stacku.
# Použití: proklik.sh <krok.js> [složka-na-screenshoty]
# jrc-dev se jen připojí do sítě jrc-audit-brightbean_web (nepřepíná se, nic se v něm neinstaluje).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
net=jrc-audit-brightbean_web
docker inspect jrc-dev --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | grep -qw "$net" \
  || docker network connect "$net" jrc-dev
# Chromium u názvu hostitele "app" vynucuje HTTPS, proto adresa IP kontejneru.
ip="$(docker inspect jrc-audit-brightbean-app-1 --format "{{(index .NetworkSettings.Networks \"$net\").IPAddress}}")"
docker cp -q "$here/pw.js" jrc-dev:/tmp/pw.js
docker cp -q "$1" jrc-dev:/tmp/step.js
docker exec -e BB_BASE="http://$ip:8000" -e BB_STATE="${BB_STATE:-/tmp/bb-state.json}" -w /tmp jrc-dev node /tmp/pw.js /tmp/step.js
if [[ -n "${2:-}" ]]; then
  mkdir -p "$2"
  docker cp -q jrc-dev:/tmp/bb-shots/. "$2/"
fi
