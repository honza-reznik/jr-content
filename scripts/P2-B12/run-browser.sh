#!/bin/sh
set -eu
[ "${JRC_PROJECT:-}" = jrc-p2b12 ] || exit 2
cd "$(dirname "$0")/../.."
trap 'docker rm -f jrc-p2b12-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=10s 600s docker run --rm --name jrc-p2b12-browser --pull never \
  --network jrc-p2b12_internal --memory=2g --memory-swap=2g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env LANG=C.UTF-8 --env LC_ALL=C.UTF-8 \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-p2b12 \
  timeout --signal=TERM --kill-after=5s 580s node --max-old-space-size=2048 scripts/P2-B12/browser.cjs
