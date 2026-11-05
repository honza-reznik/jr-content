#!/bin/sh
# Spouštět až po přípravě vlastního projektu, migracích a synchronizaci FreeFrame.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b18 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p1b18' >&2; exit 2; }
timeout 600s docker run --rm --name jrc-p1b18-browser --pull never --network host \
  --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 \
  --user "$(id -u):$(id -g)" --env JRC_PROJECT=jrc-p1b18 \
  --volume "$PWD:/workspace" --workdir /workspace jrc-dev:jrc-p1b18 \
  timeout 580s node --max-old-space-size=2048 scripts/P1-B18/video-without-audio.cjs
