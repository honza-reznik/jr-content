#!/bin/sh
# Celá sada Brightbean po dávkách ve vlastním projektu jrc-p2b13 (stejné volby jako make test-bb).
# Výstup každé dávky: ops/test-output/P2-B13/bb-<n>.log; souhrn na konci.
set -u
[ "${JRC_PROJECT:-}" = jrc-p2b13 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p2b13' >&2; exit 2; }
cd "$(dirname "$0")/../.."
out=ops/test-output/P2-B13
container=jrc-p2b13-jrc-bb-web-1
run() {
  name=$1; shift
  timeout 3600 docker exec "$container" pytest -p no:cacheprovider -o addopts="" -q --tb=short \
    --ds=config.settings.jrc_test "$@" > "$out/bb-$name.log" 2>&1
  printf '%s: %s\n' "$name" "$(tail -1 "$out/bb-$name.log")"
}
docker exec "$container" sh -c 'ruff check . && ruff format --check .' > "$out/bb-ruff.log" 2>&1
printf 'ruff: %s\n' "$(tail -1 "$out/bb-ruff.log")"
jrc=$(cd brightbean && ls -d apps/jrc_*)
run 1-jrc $jrc
run 2-composer-calendar-approvals apps/composer apps/calendar apps/approvals
rest=$(cd brightbean && ls -d apps/*/ | sed 's#/$##' | grep -v '^apps/jrc_' | grep -v -x -e apps/composer -e apps/calendar -e apps/approvals)
run 3-ostatni $rest tests providers
