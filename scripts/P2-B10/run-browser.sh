#!/bin/sh
# Pouze spuštěný vlastní projekt jrc-p2b10, bez sestavení a bez stahování; Chromium z obrazu jrc-dev.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p2b10 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p2b10' >&2; exit 2; }
cd "$(dirname "$0")/../.."
# I když vyprší vnější limit, prohlížeč i jeho potomci zmizí.
trap 'docker rm -f jrc-p2b10-browser >/dev/null 2>&1 || true' EXIT HUP INT TERM
timeout --signal=TERM --kill-after=5s 595s docker run --rm --name jrc-p2b10-browser --pull never \
  --network host --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 --shm-size=256m \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-p2b10 \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-p2b10 \
  timeout --signal=TERM --kill-after=5s 580s node --max-old-space-size=2048 scripts/P2-B10/${JRC_BROWSER_SCRIPT:-browser.cjs} "$@"
