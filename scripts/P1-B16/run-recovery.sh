#!/bin/sh
# Jen vlastní projekt; žádný build ani pull. Časové limity i při selhání.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b16 ] || exit 2
compose() { docker compose --project-name jrc-p1b16 --file docker-compose.yml --env-file .env "$@"; }
for phase in idle commit; do
  mkdir -p freeframe/.pytest_cache/p1b16
  rm -f "freeframe/.pytest_cache/p1b16/$phase-waiting" "freeframe/.pytest_cache/p1b16/$phase-restarted"
  timeout 280 docker exec -e PYTHONPATH=/workspace -e JRC_RECOVERY_PHASE="$phase" jrc-p1b16-jrc-ff-api-1 timeout 270 python /jrc-scripts/P1-B16/restart-recovery.py > "ops/test-output/P1-B16/recovery-$phase.log" 2>&1 &
  runner=$!
  counter=0
  while [ ! -f "freeframe/.pytest_cache/p1b16/$phase-waiting" ]; do
    kill -0 "$runner" || { wait "$runner"; exit 1; }
    counter=$((counter + 1))
    [ "$counter" -lt 90 ] || { kill "$runner"; exit 1; }
    sleep 1
  done
  compose restart jrc-postgres
  timeout 60 sh -c 'until docker exec jrc-p1b16-postgres pg_isready -U jrc -d jrc >/dev/null; do sleep 1; done'
  touch "freeframe/.pytest_cache/p1b16/$phase-restarted"
  wait "$runner"
  cat "ops/test-output/P1-B16/recovery-$phase.log"
done
