#!/bin/sh
# P1-B01: průchod používá jen již spuštěný vlastní projekt, bez resetu dat.
set -eu
case "${JRC_PROJECT:-}" in
  jrc-p1b01) block=P1-B01; app=13443; s3=14163 ;;
  jrc-p1b03) block=P1-B03; app=13463; s3=14183 ;;
  *) exit 2 ;;
esac
compose() { docker compose --project-name "$JRC_PROJECT" --file docker-compose.yml --env-file .env "$@"; }
compose exec -T jrc-ff-api python /jrc-scripts/F10-B02/check-cors.py
mkdir -p "ops/test-output/$block" "ops/screenshoty/$block"
compose exec -T -e JRC_PROJECT="$JRC_PROJECT" -e JRC_E2E_PROJECT="$JRC_PROJECT" jrc-bb-web python manage.py shell \
  < scripts/F10-B02/seed.py | tail -1 > "ops/test-output/$block/fixture.json"
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT="$JRC_PROJECT" --env JRC_E2E_PROJECT="$JRC_PROJECT" \
  --env JRC_E2E_APP_PORT="$app" --env JRC_E2E_S3_PORT="$s3" --env JRC_E2E_HTTPS \
  --env JRC_E2E_OUTPUT="ops/test-output/$block" --env JRC_E2E_SCREENSHOTS="ops/screenshoty/$block" \
  --volume "$PWD:/jrc" --workdir /jrc jrc-dev:"$JRC_PROJECT" node scripts/F10-B02/reel-for-ella.cjs
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_E2E_OUTPUT="ops/test-output/$block" --volume "$PWD:/jrc" --workdir /jrc \
  jrc-dev:"$JRC_PROJECT" node scripts/P1-B01/check-paths.cjs
