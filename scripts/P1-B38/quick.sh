#!/bin/sh
# Cílené testy rychle (bez dávkování), jen vlastní projekt.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b38 ] || exit 2
exec sh scripts/P1-B38/run-tests.sh pytest -p no:cacheprovider -o addopts="" -q --tb=short --reuse-db --ds=config.settings.jrc_test "$@"
