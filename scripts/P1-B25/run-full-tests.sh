#!/bin/sh
# Each shard runs in its own bounded container; all collected tests occur once.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b25 ] || exit 2
results=ops/test-output/P1-B25
: > "$results/full-summary.tsv"
failed=0
for manifest in "$results"/shards/shard-*.txt; do
    name=$(basename "$manifest" .txt)
    status=0
    sh scripts/P1-B25/run-tests.sh pytest -p no:cacheprovider -o addopts="" -q --tb=short \
        --reuse-db --ds=config.settings.jrc_test "@/p1b25-results/shards/$name.txt" \
        > "$results/full-$name.log" 2>&1 || status=$?
    printf '%s\t%s\n' "$name" "$status" >> "$results/full-summary.tsv"
    [ "$status" = 0 ] || failed=1
done
exit "$failed"
