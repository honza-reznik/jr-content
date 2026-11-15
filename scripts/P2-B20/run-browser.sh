#!/bin/sh
# P2-B20: jen spuštěný vlastní projekt jrc-p2b20, bez sestavení a bez stahování; Chromium z obrazu jrc-dev.
# Použití: JRC_PROJECT=jrc-p2b20 scripts/P2-B20/run-browser.sh upload|reuse
set -eu
[ "${JRC_PROJECT:-}" = jrc-p2b20 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p2b20' >&2; exit 2; }
cd "$(dirname "$0")/../.."
trap 'docker rm -f jrc-p2b20-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=10s 600s docker run --rm --name jrc-p2b20-browser --pull never \
  --network host --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-p2b20 \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-p2b20 \
  timeout --signal=TERM --kill-after=5s 580s node --max-old-space-size=2048 scripts/P2-B20/browser.cjs "$@"
