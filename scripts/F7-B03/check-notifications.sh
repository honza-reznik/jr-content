#!/bin/sh
# F7-B03: živé ověření seznamu upozornění. Jen projekt jrc-f7b03 (porty 13403/14123/15653/18223).
# Použití: JRC_PROJECT=jrc-f7b03 sh scripts/F7-B03/check-notifications.sh   (po make up s těmito porty)
set -eu
[ "${JRC_PROJECT:-}" = "jrc-f7b03" ] || { echo 'Jen JRC_PROJECT=jrc-f7b03' >&2; exit 2; }
mkdir -p ops/test-output/F7-B03
COMPOSE="docker compose --project-name jrc-f7b03 --file docker-compose.yml --env-file .env"
$COMPOSE exec -T -e JRC_PROJECT=jrc-f7b03 jrc-bb-web python manage.py shell < scripts/F7-B03/seed.py \
  | tail -1 > ops/test-output/F7-B03/fixture.json
# Brightbean omezuje přihlášení z jedné adresy na 10 za minutu (počítadlo v paměti procesu).
sleep 65
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT=jrc-f7b03 --volume "$PWD:/jrc" --workdir /jrc \
  "jrc-dev:jrc-f7b03" node scripts/F7-B03/check-notifications.cjs
