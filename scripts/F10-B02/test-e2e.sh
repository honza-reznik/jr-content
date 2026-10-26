#!/bin/sh
# Celý scénář Reel pro Ellu. Reset i úklid míří výhradně na jrc-f10b02.
set -eu
if [ "${JRC_PROJECT:-}" = jrc-p1b01 ] || [ "${JRC_PROJECT:-}" = jrc-p1b03 ]; then
  exec sh scripts/P1-B01/test-e2e.sh
fi
[ "${JRC_PROJECT:-}" = "jrc-f10b02" ] || { echo 'Pouze JRC_PROJECT=jrc-f10b02' >&2; exit 2; }
[ -f .env ] || JRC_PROJECT=jrc-f10b02 JRC_APP_PORT=13433 JRC_S3_PORT=14153 \
  JRC_POSTGRES_PORT=15683 JRC_BB_PORT=18253 make env
[ -f .env ] || { echo 'Chybí .env; spusťte JRC_PROJECT=jrc-f10b02 make up' >&2; exit 2; }
for pair in 'JRC_APP_PORT 13433' 'JRC_S3_PORT 14153' 'JRC_POSTGRES_PORT 15683' 'JRC_BB_PORT 18253'; do
  set -- $pair
  value=$(awk -F= -v key="$1" '$1 == key {print $2}' .env)
  [ "$value" = "$2" ] || { echo "V .env má být $1=$2 (je $value)" >&2; exit 2; }
done

compose() { docker compose --project-name jrc-f10b02 --file docker-compose.yml --env-file .env "$@"; }
cleanup() {
  set +e
  JRC_PROJECT=jrc-f10b02 make down >/dev/null 2>&1
  compose down --volumes --remove-orphans >/dev/null 2>&1
  docker image rm \
    jrc-dev:jrc-f10b02 jrc-bb:jrc-f10b02 jrc-s3:jrc-f10b02 \
    jrc-ingress:jrc-f10b02 jrc-ff-api:jrc-f10b02 jrc-ff-web:jrc-f10b02 >/dev/null 2>&1
}
trap cleanup EXIT HUP INT TERM

# Každý běh začíná prázdnou DB a úložištěm v našem ověřovacím projektu.
JRC_PROJECT=jrc-f10b02 make down >/dev/null 2>&1 || true
compose down --volumes --remove-orphans >/dev/null
JRC_PROJECT=jrc-f10b02 JRC_APP_PORT=13433 JRC_S3_PORT=14153 JRC_POSTGRES_PORT=15683 JRC_BB_PORT=18253 make up

# API se hlásí zdravé dřív, než její startovní vlákno nastaví CORS na jrc-video.
# Nezakládejme scénář, dokud bucket výslovně nepovoluje právě původ z .env.
attempt=0
until compose exec -T jrc-ff-api python /jrc-scripts/F10-B02/check-cors.py; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo 'CORS bucketu jrc-video se do 30 sekund neshodovalo s původem aplikace.' >&2
    exit 1
  fi
  sleep 1
done

mkdir -p ops/test-output/F10-B02 ops/screenshoty/F10-B02
compose exec -T -e JRC_PROJECT=jrc-f10b02 jrc-bb-web python manage.py shell \
  < scripts/F10-B02/seed.py | tail -1 > ops/test-output/F10-B02/fixture.json
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT=jrc-f10b02 \
  --volume "$PWD:/jrc" --workdir /jrc \
  jrc-dev:jrc-f10b02 node scripts/F10-B02/reel-for-ella.cjs

echo 'OK: E2E Reel pro Ellu; nyní se uklízí projekt a obrazy jrc-f10b02.'
