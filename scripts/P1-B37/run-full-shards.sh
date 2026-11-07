#!/bin/sh
# Each shard runs in its own bounded container; all collected tests occur once.
# SHARDS: manifest directory under ops/test-output/P1-B37 (whole test files, no
# parametrized IDs, so random parameters cannot break a manifest). PREFIX: log names.
# The first shard recreates the shared test database, the others reuse it; never run
# two copies at once (the test database name is fixed in config/settings/test.py).
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b37 ] || exit 2
results=ops/test-output/P1-B37
shards=${SHARDS:-shards}
prefix=${PREFIX:-full}
: > "$results/$prefix-summary.tsv"
failed=0
db=--create-db
for manifest in "$results/$shards"/shard-*.txt; do
    name=$(basename "$manifest" .txt)
    status=0
    sh scripts/P1-B37/run-tests.sh pytest -p no:cacheprovider -o addopts="" -q --tb=short \
        --reuse-db $db --ds=config.settings.jrc_test "@/p1b37-results/$shards/$name.txt" \
        > "$results/$prefix-$name.log" 2>&1 || status=$?
    db=
    printf '%s\t%s\t%s\n' "$name" "$status" "$(tail -1 "$results/$prefix-$name.log")" >> "$results/$prefix-summary.tsv"
    [ "$status" = 0 ] || failed=1
done
exit "$failed"
