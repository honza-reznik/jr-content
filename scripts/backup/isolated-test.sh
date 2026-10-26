#!/bin/sh
# P1 review: samostatná kopie aktuálních změn, vlastní .env a dočasné projekty.
set -eu
script=$1
mkdir -p .cache
snapshot=$(mktemp -d "$PWD/.cache/p1-backup-XXXXXXXX")
tar --exclude=./.cache --exclude=./.git --exclude=./.env --exclude=./backups \
    --exclude=node_modules --exclude=.next --exclude=./ops/test-output --exclude=./ops/screenshoty \
    -cf - . | tar -xf - -C "$snapshot"
cd "$snapshot"
export JRC_ISOLATED_TEST=1
export JRC_PROJECT="${JRC_PROJECT}-$(basename "$snapshot" | tr A-Z a-z)"
export JRC_BASE_PATH="${JRC_BASE_PATH-/jrc}"
export JRC_APP_PORT=13493 JRC_S3_PORT=14213 JRC_POSTGRES_PORT=15743 JRC_BB_PORT=18313
sh "$script"
