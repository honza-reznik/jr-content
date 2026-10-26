#!/bin/sh
# P1-B01: průchod používá jen již spuštěný vlastní projekt, bez resetu dat.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b01 ] || exit 2
compose() { docker compose --project-name "$JRC_PROJECT" --file docker-compose.yml --env-file .env "$@"; }
compose exec -T jrc-ff-api python /jrc-scripts/F10-B02/check-cors.py
mkdir -p ops/test-output/P1-B01 ops/screenshoty/P1-B01
compose exec -T -e JRC_PROJECT=jrc-p1b01 -e JRC_E2E_PROJECT=jrc-p1b01 jrc-bb-web python manage.py shell \
  < scripts/F10-B02/seed.py | tail -1 > ops/test-output/P1-B01/fixture.json
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT=jrc-p1b01 --env JRC_E2E_PROJECT=jrc-p1b01 \
  --env JRC_E2E_APP_PORT=13443 --env JRC_E2E_S3_PORT=14163 --env JRC_E2E_HTTPS \
  --env JRC_E2E_OUTPUT=ops/test-output/P1-B01 --env JRC_E2E_SCREENSHOTS=ops/screenshoty/P1-B01 \
  --volume "$PWD:/jrc" --workdir /jrc jrc-dev:jrc-p1b01 node scripts/F10-B02/reel-for-ella.cjs
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --volume "$PWD:/jrc" --workdir /jrc \
  jrc-dev:jrc-p1b01 node scripts/P1-B01/check-paths.cjs
