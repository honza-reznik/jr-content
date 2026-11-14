#!/bin/sh
# Celá sada BB, sběr všech testů a dvanáct menších dávek. Jen vlastní Docker projekt.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p2b10 ] || exit 2
cd "$(dirname "$0")/../.."
out=ops/test-output/P2-B10
container=jrc-p2b10-jrc-bb-web-1
mkdir -p "$out"
docker exec "$container" timeout 60s sh -c 'ruff check . && ruff format --check .' > "$out/bb-ruff.log" 2>&1
docker exec -e NODE_OPTIONS=--max-old-space-size=2048 "$container" timeout 120s python /jrc-scripts/P2-B10/bb-suite.py collect > "$out/bb-collect.log" 2>&1
tail -1 "$out/bb-collect.log"
failed=0
for batch in 0 1 2 3 4 5 6 7 8 9 10 11; do
  if docker exec -e NODE_OPTIONS=--max-old-space-size=2048 "$container" timeout 600s python /jrc-scripts/P2-B10/bb-suite.py "$batch" > "$out/bb-$batch.log" 2>&1; then
    printf 'Dávka %s: ' "$batch"
  else
    failed=1
    printf 'CHYBA dávky %s: ' "$batch"
  fi
  tail -1 "$out/bb-$batch.log"
done
exit "$failed"
