#!/bin/sh
# P1-B12: integrační test nad již připraveným, postradatelným projektem jrc-p1b12.
# Obrazy přeznačit z :jrc, .env vytvořit přes make env, up pouze --no-build --pull never.
# Test nic nesestavuje ani nestahuje; na konci smaže jen kontejnery a svazky jrc-p1b12.
set -eu
umask 077
[ "${JRC_PROJECT:-}" = jrc-p1b12 ] || { echo 'Použij JRC_PROJECT=jrc-p1b12.' >&2; exit 2; }
project=$JRC_PROJECT
export JRC_BACKUP_KEEP=7
compose() {
    JRC_PROJECT="$project" docker compose --project-name "$project" --file docker-compose.yml --env-file .env "$@"
}
cleanup() {
    status=$?
    trap - EXIT
    compose down --volumes >ops/test-output/P1-B12/cleanup.log 2>&1 || exit 1
    exit "$status"
}
mkdir -p ops/test-output/P1-B12 ops/screenshoty/P1-B12
sh scripts/workspace.sh check
JRC_PROJECT="$project" make check-project-ports
trap cleanup EXIT
step() { compose exec -T jrc-dev python3 scripts/backup/check-incremental.py "$1"; }
step seed
# Jen nová vymyšlená data; výstup obsahuje heslo testovacího účtu, je mimo Git a KB.
compose exec -T jrc-bb-web sh -c 'python manage.py shell < /jrc-scripts/backup/seed-bb.py' \
    | tail -n 1 > ops/test-output/P1-B12/seed.json
make backup
make backup
step unchanged
compose exec -T jrc-dev python3 scripts/backup/jrc_backup.py inventory > ops/test-output/P1-B12/before.json
step change
make backup
step changed
second=$(compose exec -T jrc-dev python3 -c 'import json; print(json.load(open("ops/test-output/P1-B12/backups.json"))["second"])')
backup="backups/$project/$second"
make backup-verify BACKUP="$backup"
# Obnova druhé zálohy po smazání první, do téhož vyprázdněného vlastního projektu.
compose down --volumes
JRC_BACKUP_NO_BUILD=yes sh scripts/backup/backup.sh restore-precheck "$backup"
JRC_BACKUP_NO_BUILD=yes sh scripts/backup/backup.sh restore-data "$backup"
compose up --detach --no-build --pull never --wait --wait-timeout 180
sh scripts/backup/backup.sh restore-finish
compose exec -T jrc-dev python3 scripts/backup/jrc_backup.py inventory > ops/test-output/P1-B12/after.json
step restored
# Prohlížeč běží uvnitř vývojového obrazu, používá pouze místní původ našeho projektu.
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
    --env-file .env --env JRC_PUBLIC_ORIGIN=http://127.0.0.1:13553 --env JRC_BASE_PATH=/jrc \
    --volume "$(pwd -P):/workspace" --workdir /workspace "jrc-dev:$project" \
    node scripts/backup/incremental-browser.cjs
echo 'OK P1-B12: inkrementální místo, změna objektu, smazání první zálohy, obnova druhé a Chromium 1440/390.'
