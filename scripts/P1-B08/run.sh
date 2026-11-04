#!/bin/sh
# P1-B08: vizuální test záložky Concept jen v projektu jrc-p1b08 (nová data při každém běhu).
set -eu
cd "$(dirname "$0")/../.."
C="docker compose --project-name jrc-p1b08 --file docker-compose.yml --env-file .env"
mkdir -p ops/test-output/P1-B08 ops/screenshoty/P1-B08
JRC_PROJECT=jrc-p1b08 $C exec -T jrc-bb-web python manage.py shell < scripts/P1-B08/seed.py | sed -n 's/^SEED=//p' > ops/test-output/P1-B08/session.json
JRC_PROJECT=jrc-p1b08 $C exec -T -e DEBUG_ROUTE="${DEBUG_ROUTE:-}" jrc-dev node scripts/P1-B08/concept.cjs
