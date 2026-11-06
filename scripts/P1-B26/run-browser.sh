#!/bin/sh
# Prohlížeč jen v Dockeru vlastního projektu jrc-p1b26, bez sestavení a bez stahování.
# Použití: JRC_PROJECT=jrc-p1b26 sh scripts/P1-B26/run-browser.sh <skript.cjs> [argumenty]
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b26 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p1b26' >&2; exit 2; }
cd "$(dirname "$0")/../.."
script=${1:?Chybí skript}; shift
limit=${JRC_BROWSER_SECONDS:-570}
media=/home/honza/jr-content-wt/TEST/ops/screenshoty/media
trap 'docker rm -f jrc-p1b26-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=10s "$((limit + 20))s" docker run --rm --name jrc-p1b26-browser --pull never \
  --network host --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-p1b26 \
  --volume "$PWD:/workspace" --volume "$media:/media:ro" --workdir /workspace jrc-dev:jrc-p1b26 \
  timeout --signal=TERM --kill-after=5s "${limit}s" node --max-old-space-size=2048 "$script" "$@"
