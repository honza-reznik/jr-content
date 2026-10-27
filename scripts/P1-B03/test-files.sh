#!/bin/sh
# Čtyři prohlížečové relace a skutečné objekty v úložišti, jen náš projekt.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b03 ] || exit 2
compose() { docker compose --project-name "$JRC_PROJECT" --file docker-compose.yml --env-file .env "$@"; }
mkdir -p ops/test-output/P1-B03
compose exec -T -e JRC_PROJECT="$JRC_PROJECT" jrc-bb-web python manage.py shell \
  < scripts/P1-B03/seed-bb.py | tail -1 > ops/test-output/P1-B03/files-bb.json
# Synchronizace má při přechodném výpadku trvalou frontu; před ověřením ji dokončíme.
compose exec -T jrc-bb-web python manage.py jrc_sync_freeframe --pending >/dev/null
compose exec -T -e JRC_PROJECT="$JRC_PROJECT" -e PYTHONPATH=/workspace jrc-ff-api python /jrc-scripts/P1-B03/seed-ff.py \
  < ops/test-output/P1-B03/files-bb.json > ops/test-output/P1-B03/files.json
chmod 600 ops/test-output/P1-B03/files*.json
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT="$JRC_PROJECT" --volume "$PWD:/jrc" --workdir /jrc \
  jrc-dev:"$JRC_PROJECT" node scripts/P1-B03/files.cjs
# Důkaz sdíleného Redis rozpočtu BB ↔ FF/HLS na skutečné instanci.
compose exec -T jrc-bb-web python manage.py shell < scripts/P1-B03/check-limits.py
