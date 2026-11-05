#!/bin/sh
# Pouze již spuštěný vlastní projekt, žádné sestavení ani stahování.
set -eu
if [ "${P1B14_TIMED:-}" != yes ]; then
    exec timeout --signal=TERM --kill-after=10s 590s env P1B14_TIMED=yes sh "$0" "$@"
fi
umask 077
cd "$(dirname "$0")/../.."
export JRC_PROJECT=jrc-p1b14
mkdir -p ops/test-output/P1-B14 ops/screenshoty/P1-B14
compose() { docker compose --project-name jrc-p1b14 --file docker-compose.yml --env-file .env "$@"; }
docker update --memory 3g --memory-swap 3g --cpus 2 jrc-p1b14-dev >/dev/null
compose exec -T jrc-bb-web python manage.py shell < scripts/P1-B14/seed.py | sed -n 's/^SEED=//p' > ops/test-output/P1-B14/session.json
chmod 600 ops/test-output/P1-B14/session.json
container=$(compose ps -q jrc-bb-web)
docker cp "$container:/tmp/p1b14-thumbnails/garden.png" ops/test-output/P1-B14/garden.png
compose exec -T jrc-dev ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=180x320:rate=12:duration=2 -c:v libx264 -threads 2 -preset ultrafast -pix_fmt yuv420p ops/test-output/P1-B14/final.mp4
compose exec -T jrc-dev timeout --signal=TERM --kill-after=5s 240s node --max-old-space-size=2048 scripts/P1-B14/check.cjs texts
compose exec -T jrc-bb-web python manage.py shell < scripts/P1-B14/approve.py
compose exec -T jrc-dev timeout --signal=TERM --kill-after=5s 240s node --max-old-space-size=2048 scripts/P1-B14/check.cjs publish
