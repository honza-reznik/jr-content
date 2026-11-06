#!/bin/sh
# Obnoví přihlašovací cookies vymyšlených účtů ve fixture.json (soubor 600, ignorovaný).
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b26 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p1b26' >&2; exit 2; }
cd "$(dirname "$0")/../.."
fixture=ops/test-output/P1-B26/fixture.json
docker cp "$fixture" jrc-p1b26-jrc-bb-web-1:/tmp/jrc-p1b26-fixture.json
docker exec -e JRC_PROJECT=jrc-p1b26 jrc-p1b26-jrc-bb-web-1 sh -c 'python manage.py shell < /jrc-scripts/P1-B26/sessions.py'
docker cp jrc-p1b26-jrc-bb-web-1:/tmp/jrc-p1b26-fixture.json "$fixture"
docker exec jrc-p1b26-jrc-bb-web-1 rm -f /tmp/jrc-p1b26-fixture.json
chmod 600 "$fixture"
