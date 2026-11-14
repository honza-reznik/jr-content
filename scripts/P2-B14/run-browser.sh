#!/bin/sh
# P2-B14: jen spuštěný vlastní projekt jrc-p2b14, bez sestavení a bez stahování; Chromium z obrazu jrc-dev.
# Použití: JRC_PROJECT=jrc-p2b14 B14_ENV=<soubor s OWNER_/CLIENT_ údaji> B14_WS=<klient> B14_POSTS=<a,b,c> scripts/P2-B14/run-browser.sh
set -eu
[ "${JRC_PROJECT:-}" = jrc-p2b14 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p2b14' >&2; exit 2; }
cd "$(dirname "$0")/../.."
trap 'docker rm -f jrc-p2b14-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=10s 600s docker run --rm --name jrc-p2b14-browser --pull never \
  --network host --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-p2b14 --env-file "$B14_ENV" \
  --env B14_WS --env B14_POSTS \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-p2b14 \
  timeout --signal=TERM --kill-after=5s 580s node --max-old-space-size=2048 scripts/P2-B14/browser.cjs "$@"
