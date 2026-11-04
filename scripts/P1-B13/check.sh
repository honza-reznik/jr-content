#!/bin/sh
# Existující projekt a obrazy, žádné sestavování ani stahování.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b13 ] || { echo 'Pouze projekt jrc-p1b13' >&2; exit 2; }
umask 077
mkdir -p ops/test-output/P1-B13
docker compose --project-name "$JRC_PROJECT" --file docker-compose.yml --env-file .env \
  exec -T -e JRC_PROJECT="$JRC_PROJECT" jrc-bb-web python manage.py shell \
  < scripts/P1-B13/seed.py > ops/test-output/P1-B13/fixture.json
run_browser() {
  docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
    --env-file .env --env JRC_PROJECT="$JRC_PROJECT" --volume "$PWD:/jrc" --workdir /jrc \
    "jrc-dev:$JRC_PROJECT" node scripts/P1-B13/check.cjs "$@"
}
run_browser
docker compose --project-name "$JRC_PROJECT" --file docker-compose.yml --env-file .env \
  exec -T -e JRC_PROJECT="$JRC_PROJECT" jrc-bb-web python manage.py shell \
  -c 'exec(open("/jrc-scripts/P1-B13/prepare_publish.py").read())' < ops/test-output/P1-B13/fixture.json
run_browser publish
