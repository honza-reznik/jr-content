#!/bin/sh
# P1-B10: pouze existující kontejnery vlastního projektu, žádné sestavení ani stažení.
set -eu
umask 077
cd "$(dirname "$0")/../.."
export JRC_PROJECT=jrc-p1b10
mkdir -p ops/test-output/P1-B10 ops/screenshoty/P1-B10
compose() { docker compose --project-name jrc-p1b10 --file docker-compose.yml --env-file .env "$@"; }
compose exec -T jrc-bb-web python manage.py shell < scripts/P1-B10/seed.py | sed -n 's/^SEED=//p' > ops/test-output/P1-B10/session.json
test -s ops/test-output/P1-B10/session.json
chmod 600 ops/test-output/P1-B10/session.json
container=$(compose ps -q jrc-bb-web)
for suffix in png jpg webp; do
    docker cp "$container:/tmp/p1b10-thumbnails/garden.$suffix" "ops/test-output/P1-B10/garden.$suffix"
done
compose exec -T jrc-dev node scripts/P1-B10/thumbnails.cjs
