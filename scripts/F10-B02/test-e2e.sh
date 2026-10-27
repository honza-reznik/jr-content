#!/bin/sh
# Celý scénář Reel pro Ellu. Reset i úklid míří výhradně na JRC_PROJECT.
set -eu
if [ "${JRC_PROJECT:-}" = jrc-p1b01 ] || [ "${JRC_PROJECT:-}" = jrc-p1b03 ]; then
  exec sh scripts/P1-B01/test-e2e.sh
fi
project=${JRC_PROJECT:-jrc-f10b02}
case "$project" in
  jrc) echo 'E2E test nesmí cílit na hlavní projekt jrc.' >&2; exit 2 ;;
  jrc-*) ;;
  *) echo 'E2E test vyžaduje JRC_PROJECT=jrc-<blok>.' >&2; exit 2 ;;
esac
port_from_env() { if [ -f .env ]; then awk -F= -v key="$1" '$1 == key {print $2}' .env; fi; }
app_port=${JRC_APP_PORT:-$(port_from_env JRC_APP_PORT)}
s3_port=${JRC_S3_PORT:-$(port_from_env JRC_S3_PORT)}
postgres_port=${JRC_POSTGRES_PORT:-$(port_from_env JRC_POSTGRES_PORT)}
bb_port=${JRC_BB_PORT:-$(port_from_env JRC_BB_PORT)}
app_port=${app_port:-13433}
s3_port=${s3_port:-14153}
postgres_port=${postgres_port:-15683}
bb_port=${bb_port:-18253}
[ -f .env ] || JRC_PROJECT="$project" JRC_APP_PORT="$app_port" JRC_S3_PORT="$s3_port" \
  JRC_POSTGRES_PORT="$postgres_port" JRC_BB_PORT="$bb_port" make env
[ -f .env ] || { echo "Chybí .env; spusťte JRC_PROJECT=$project make test-up" >&2; exit 2; }
for pair in "JRC_APP_PORT $app_port" "JRC_S3_PORT $s3_port" "JRC_POSTGRES_PORT $postgres_port" "JRC_BB_PORT $bb_port"; do
  set -- $pair
  value=$(awk -F= -v key="$1" '$1 == key {print $2}' .env)
  [ "$value" = "$2" ] || { echo "V .env má být $1=$2 (je $value)" >&2; exit 2; }
done

export JRC_PROJECT="$project"
compose() { docker compose --project-name "$project" --file docker-compose.yml --env-file .env "$@"; }
cleanup() {
  set +e
  JRC_PROJECT="$project" make down >/dev/null 2>&1
  compose down --volumes --remove-orphans >/dev/null 2>&1
  docker image rm \
    "jrc-dev:$project" "jrc-bb:$project" "jrc-s3:$project" \
    "jrc-ingress:$project" "jrc-ff-api:$project" "jrc-ff-web:$project" >/dev/null 2>&1
}
trap cleanup EXIT HUP INT TERM

# Každý běh začíná prázdnou DB a úložištěm v našem ověřovacím projektu.
JRC_PROJECT="$project" make down >/dev/null 2>&1 || true
compose down --volumes --remove-orphans >/dev/null
JRC_PROJECT="$project" JRC_APP_PORT="$app_port" JRC_S3_PORT="$s3_port" \
  JRC_POSTGRES_PORT="$postgres_port" JRC_BB_PORT="$bb_port" make test-up

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
compose exec -T -e JRC_PROJECT="$project" jrc-bb-web python manage.py shell \
  < scripts/F10-B02/seed.py | tail -1 > ops/test-output/F10-B02/fixture.json
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT="$project" \
  --volume "$PWD:/jrc" --workdir /jrc \
  "jrc-dev:$project" node scripts/F10-B02/reel-for-ella.cjs

echo "OK: E2E Reel pro Ellu; nyní se uklízí projekt a obrazy $project."
