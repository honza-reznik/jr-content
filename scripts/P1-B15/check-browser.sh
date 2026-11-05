#!/bin/sh
# Chromium pouze v Dockeru, pevný limit paměti, nejvýše deset minut.
set -eu
if [ "${1:-}" != --bounded ]; then
  exec timeout 600 sh "$0" --bounded
fi
[ "${JRC_PROJECT:-}" = jrc-p1b15 ] || { echo 'Použij JRC_PROJECT=jrc-p1b15.' >&2; exit 1; }
mkdir -p ops/test-output/P1-B15 ops/screenshoty/P1-B15
JRC_PROJECT=jrc-p1b15 docker compose --project-name jrc-p1b15 --file docker-compose.yml --env-file .env \
  exec -T jrc-bb-web timeout 60 python manage.py shell < scripts/P1-B15/seed.py > ops/test-output/P1-B15/seed.log
chmod 600 ops/test-output/P1-B15/seed.log
timeout 600 docker run --rm --name jrc-p1b15-browser --init --pull never --network host \
  --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --volume "$PWD:/workspace" --workdir /workspace \
  jrc-dev:jrc-p1b15 timeout 540 node --max-old-space-size=2048 scripts/P1-B15/browser.cjs
