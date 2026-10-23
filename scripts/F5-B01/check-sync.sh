#!/bin/sh
# Řídí jen vlastní Docker projekt; aplikace i všechny kontroly běží uvnitř něj.
set -eu
test "${JRC_PROJECT:-}" = jrc-f5b01 || { echo 'Použij JRC_PROJECT=jrc-f5b01'; exit 1; }
for stage in initial changed archived deleted; do
  docker compose --env-file .env exec -T jrc-bb-web python - "$stage" < scripts/F5-B01/check-sync.py
  docker compose --env-file .env exec -T -e PYTHONPATH=/workspace jrc-ff-api python /jrc-scripts/F5-B01/check-permissions.py "$stage"
done
