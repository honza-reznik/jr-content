#!/bin/sh
# Pouze spuštěný vlastní projekt, bez sestavení a bez stahování.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b28 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p1b28' >&2; exit 2; }
cd "$(dirname "$0")/../.."
# Even when the outer timeout fires, remove the browser and all its children.
trap 'docker rm -f jrc-p1b28-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=10s 590s docker run --rm --name jrc-p1b28-browser --pull never \
  --network host --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-p1b28 \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-p1b28 \
  timeout --signal=TERM --kill-after=5s 570s node --max-old-space-size=2048 scripts/P1-B28/layout.cjs "$@"
