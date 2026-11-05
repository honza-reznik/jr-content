#!/bin/sh
# P1-B11: Chromium průchod nového uživatele jen v projektu jrc-p1b11 (nová data při každém běhu).
set -eu
cd "$(dirname "$0")/../.."
C="docker compose --project-name jrc-p1b11 --file docker-compose.yml --env-file .env"
mkdir -p ops/test-output/P1-B11 ops/screenshoty/P1-B11
JRC_PROJECT=jrc-p1b11 $C exec -T jrc-bb-web python manage.py shell < scripts/P1-B11/seed.py | sed -n 's/^SEED=//p' > ops/test-output/P1-B11/session.json
JRC_PROJECT=jrc-p1b11 $C exec -T jrc-dev node scripts/P1-B11/terms.cjs
