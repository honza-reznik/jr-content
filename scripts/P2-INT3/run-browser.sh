#!/bin/sh
# P2-INT3: jen spuštěný vlastní projekt jrc-int3, bez sestavení a bez stahování; Chromium z obrazu jrc-dev.
# Použití: JRC_PROJECT=jrc-int3 scripts/P2-INT3/run-browser.sh upload|check
set -eu
[ "${JRC_PROJECT:-}" = jrc-int3 ] || { echo 'Vyžadován JRC_PROJECT=jrc-int3' >&2; exit 2; }
cd "$(dirname "$0")/../.."
trap 'docker rm -f jrc-int3-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=10s 600s docker run --rm --name jrc-int3-browser --pull never \
  --network host --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-int3 --env LANG=C.UTF-8 \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-int3 \
  timeout --signal=TERM --kill-after=5s 580s node --max-old-space-size=2048 scripts/P2-INT3/browser.cjs "$@"
