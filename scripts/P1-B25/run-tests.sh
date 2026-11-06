#!/bin/sh
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b25 ] || exit 2
# Limits apply to pytest and every Node subprocess used by text services.
limit_file=$(mktemp /tmp/p1b25-limits.XXXXXX.yml)
trap 'rm -f "$limit_file"' EXIT
cat > "$limit_file" <<'YAML'
services:
  jrc-bb-web:
    mem_limit: 3g
    memswap_limit: 3g
    cpus: 2
    pids_limit: 256
    healthcheck:
      disable: true
YAML
timeout 600s docker compose -p "$JRC_PROJECT" -f docker-compose.yml -f "$limit_file" --env-file .env \
  run --rm --no-deps --pull never --name "${JRC_TEST_NAME:-jrc-p1b25-tests}" \
  --volume "$(pwd)/ops/test-output/P1-B25:/p1b25-results" \
  -e NODE_OPTIONS=--max-old-space-size=2048 jrc-bb-web timeout -k 10s 540s "$@"
