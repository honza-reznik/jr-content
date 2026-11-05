#!/bin/sh
# Regrese review1 nad už běžícím vlastním projektem; žádný build ani pull.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b16 ] || exit 2
case "${1:-all}" in api|bb|video|web|checks|all) ;; *) exit 2 ;; esac
mkdir -p ops/test-output/P1-B16
docker ps -q --filter label=com.docker.compose.project=jrc-p1b16 | \
  xargs -r docker update --memory=3g --memory-swap=3g --cpus=2 --pids-limit=256 >/dev/null
if [ "${1:-all}" = api ] || [ "${1:-all}" = all ]; then
  timeout 600 docker exec -e STALE_UPLOAD_TIMEOUT_HOURS=24 -e SOFT_DELETE_RETENTION_DAYS=30 \
    -e DISABLE_DOCS=false -e CORS_DEV_ORIGINS=true -e S3_PUBLIC_PATH= jrc-p1b16-jrc-ff-api-1 \
    timeout 580 python -m pytest apps/api/tests -q -p no:cacheprovider \
    > ops/test-output/P1-B16/review-ff-full.log 2>&1
  tail -1 ops/test-output/P1-B16/review-ff-full.log
fi
if [ "${1:-all}" = bb ] || [ "${1:-all}" = all ]; then
  timeout 1800 docker exec jrc-p1b16-jrc-bb-web-1 timeout 1770 pytest \
    -p no:cacheprovider -o addopts= -q --tb=short --ds=config.settings.jrc_test --reuse-db --create-db \
    > ops/test-output/P1-B16/review-bb-full.log 2>&1
  tail -1 ops/test-output/P1-B16/review-bb-full.log
fi
if [ "${1:-all}" = web ] || [ "${1:-all}" = all ]; then
  timeout 600 docker exec -e NEXT_PUBLIC_BASE_PATH= -e JRC_PUBLIC_ORIGIN= \
    -e NODE_OPTIONS=--max-old-space-size=2048 jrc-p1b16-jrc-ff-web-1 \
    timeout 580 pnpm test --maxWorkers=1 > ops/test-output/P1-B16/review-web-full.log 2>&1
  tail -5 ops/test-output/P1-B16/review-web-full.log
fi
if [ "${1:-all}" = video ] || [ "${1:-all}" = all ]; then
  timeout 240 docker exec jrc-p1b16-jrc-bb-web-1 timeout 230 pytest \
    -p no:cacheprovider -o addopts= -q --tb=short --ds=config.settings.jrc_test --reuse-db \
    apps/jrc_video/tests/test_upload.py apps/jrc_video/tests/test_player.py \
    > ops/test-output/P1-B16/review-bb-video.log 2>&1
  tail -1 ops/test-output/P1-B16/review-bb-video.log
fi
if [ "${1:-all}" = checks ] || [ "${1:-all}" = all ]; then
  timeout 60 docker exec jrc-p1b16-jrc-bb-web-1 \
    sh -c 'timeout 40 ruff check . && timeout 15 ruff format --check .' \
    > ops/test-output/P1-B16/review-bb-checks.log 2>&1
  timeout 180 docker exec -e NODE_OPTIONS=--max-old-space-size=2048 jrc-p1b16-jrc-ff-web-1 \
    sh -c 'timeout 100 pnpm exec tsc --noEmit && timeout 60 pnpm lint' \
    > ops/test-output/P1-B16/review-web-checks.log 2>&1
fi
