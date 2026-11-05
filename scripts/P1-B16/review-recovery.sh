#!/bin/sh
# Jen vlastní projekt, bez sestavení a stahování; omezené kontejnery i čas.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b16 ] || exit 2
case "${1:-all}" in
  all) phases='rollback terminal' ;;
  rollback|terminal) phases=$1 ;;
  *) exit 2 ;;
esac
compose() { timeout 90 docker compose --project-name jrc-p1b16 --file docker-compose.yml --env-file .env "$@"; }
root=freeframe/.pytest_cache/p1b16-review
mkdir -p "$root" ops/test-output/P1-B16
docker ps -q --filter label=com.docker.compose.project=jrc-p1b16 | \
  xargs -r docker update --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 >/dev/null
# Workery drží importy v paměti, proto načti opravený kód před živou regresí.
compose restart jrc-ff-worker jrc-ff-maintenance jrc-ff-beat
for phase in $phases; do
  rm -f "$root/$phase-waiting" "$root/$phase-stopped" "$root/$phase-recorded" "$root/$phase-recovered"
  timeout 280 docker exec -e PYTHONPATH=/workspace -e JRC_RECOVERY_PHASE="$phase" \
    jrc-p1b16-jrc-ff-api-1 timeout 270 python /jrc-scripts/P1-B16/review-recovery.py \
    > "ops/test-output/P1-B16/review-live-$phase.log" 2>&1 &
  runner=$!
  counter=0
  while [ ! -f "$root/$phase-waiting" ]; do
    kill -0 "$runner" || { wait "$runner"; exit 1; }
    counter=$((counter + 1))
    [ "$counter" -lt 90 ] || { kill "$runner"; exit 1; }
    sleep 1
  done
  compose stop jrc-postgres
  touch "$root/$phase-stopped"
  counter=0
  while [ ! -f "$root/$phase-recorded" ]; do
    kill -0 "$runner" || { wait "$runner"; exit 1; }
    counter=$((counter + 1))
    [ "$counter" -lt 90 ] || { kill "$runner"; exit 1; }
    sleep 1
  done
  compose up --detach --no-build --pull never --wait --wait-timeout 60 jrc-postgres
  touch "$root/$phase-recovered"
  wait "$runner"
  cat "ops/test-output/P1-B16/review-live-$phase.log"
done
