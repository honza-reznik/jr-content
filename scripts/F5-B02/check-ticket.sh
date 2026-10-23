#!/bin/sh
# Živé ověření vstupenky (F5-B02); běží jen ve vlastním Docker projektu jrc-f5b02.
set -eu
test "${JRC_PROJECT:-}" = jrc-f5b02 || { echo 'Použij JRC_PROJECT=jrc-f5b02'; exit 1; }
compose="docker compose --project-name jrc-f5b02 --env-file .env"
$compose exec -T jrc-redis redis-cli CONFIG GET appendfsync | grep -qx always
echo 'OK jrc-redis: appendfsync always (použité vstupenky přežijí pád stroje)'
$compose exec -T jrc-bb-web python - < scripts/F5-B02/check-ticket.py
# Vstupenky ani tokeny se nesmí objevit v logu žádné aplikace.
if $compose logs --no-color jrc-ff-api jrc-bb-web jrc-proxy 2>&1 | grep -E 'v1\.eyJ|eyJhbGci' >/dev/null; then
  echo 'CHYBA: vstupenka nebo token v logu'; exit 1
fi
echo 'OK logy bez vstupenek a tokenů'
