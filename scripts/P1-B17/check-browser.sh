#!/bin/sh
# Vlastní kontejner omezuje i paměť procesů Chromia; bez sestavení a stahování.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b17 ] || exit 2
case "${1:-}" in upload|verify) ;; *) exit 2 ;; esac
mkdir -p ops/test-output/P1-B17 ops/screenshoty/P1-B17
timeout 600 docker run --rm --name jrc-p1b17-browser --init --pull never --network host \
  --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --volume "$PWD:/workspace" --workdir /workspace \
  jrc-dev:jrc-p1b17 timeout 540 node --max-old-space-size=2048 scripts/P1-B17/browser.cjs "$1"
