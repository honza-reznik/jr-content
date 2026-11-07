#!/bin/sh
# Pouze spuštěný vlastní projekt, bez sestavení a bez stahování.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b31 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p1b31' >&2; exit 2; }
cd "$(dirname "$0")/../.."
JRC_APP_PORT=$(sed -n "s/^JRC_APP_PORT=//p" .env)
export JRC_APP_PORT
# Even when the outer timeout fires, remove the browser and all its children.
trap 'docker rm -f jrc-p1b31-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=10s 590s docker run --rm --name jrc-p1b31-browser --pull never \
  --network host --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-p1b31 --env JRC_APP_PORT="$JRC_APP_PORT" \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-p1b31 \
  timeout --signal=TERM --kill-after=5s 570s node --max-old-space-size=2048 scripts/P1-B31/${JRC_BROWSER_SCRIPT:-layout.cjs} "$@"
