#!/bin/sh
# Review1: místní obrazy, oddělený projekt, omezená paměť i čas.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b17 ] || exit 2
case "${1:-targeted}" in prepare|targeted|api|bb|web|checks|cleanup) ;; *) exit 2 ;; esac
set -- "${1:-targeted}"
out=ops/test-output/P1-B17
mkdir -p "$out"
override="$out/review-resources.yml"
if [ "$1" = prepare ]; then
  for image in jrc-dev jrc-bb jrc-ff-api jrc-ff-web jrc-ingress jrc-s3; do
    docker image tag "$image:jrc" "$image:$JRC_PROJECT"
  done
  printf 'services:\n' > "$override"
  docker compose --project-name "$JRC_PROJECT" -f docker-compose.yml config --services |
    while IFS= read -r service; do
      printf '  %s:\n    mem_limit: 3g\n    memswap_limit: 3g\n    cpus: 2\n    pids_limit: 256\n' "$service" >> "$override"
    done
  # Timeout runs the actual Docker process, not a shell function.
  timeout 300 docker compose --project-name "$JRC_PROJECT" -f docker-compose.yml -f "$override" \
    up -d --no-build --pull never jrc-ff-api jrc-bb-web jrc-ff-web > "$out/review-prepare.log" 2>&1
elif [ "$1" = targeted ]; then
  timeout 180 docker exec -e S3_PUBLIC_PATH= jrc-p1b17-jrc-ff-api-1 timeout 170 python -m pytest \
    apps/api/tests/test_abort_finished_upload.py apps/api/tests/test_pending_transcodes.py \
    apps/api/tests/test_processing_lifecycle.py apps/api/tests/test_jrc_comment_privacy.py \
    apps/api/tests/test_recovery_concurrency.py \
    apps/api/tests/test_comment_reply_integrity.py apps/api/tests/test_share_comment_replies.py \
    -q -p no:cacheprovider > "$out/review-targeted.log" 2>&1
  tail -3 "$out/review-targeted.log"
elif [ "$1" = api ]; then
  timeout 600 docker exec -e STALE_UPLOAD_TIMEOUT_HOURS=24 -e SOFT_DELETE_RETENTION_DAYS=30 \
    -e DISABLE_DOCS=false -e CORS_DEV_ORIGINS=true -e S3_PUBLIC_PATH= jrc-p1b17-jrc-ff-api-1 \
    timeout 580 python -m pytest apps/api/tests -q -p no:cacheprovider > "$out/review-ff-full.log" 2>&1
  tail -3 "$out/review-ff-full.log"
elif [ "$1" = bb ]; then
  timeout 1800 docker exec jrc-p1b17-jrc-bb-web-1 timeout 1770 pytest \
    -p no:cacheprovider -o addopts= -q --tb=short --ds=config.settings.jrc_test \
    > "$out/review-bb-full.log" 2>&1
  tail -3 "$out/review-bb-full.log"
elif [ "$1" = web ]; then
  timeout 600 docker exec -e NEXT_PUBLIC_BASE_PATH= -e JRC_PUBLIC_ORIGIN= \
    -e NODE_OPTIONS=--max-old-space-size=2048 jrc-p1b17-jrc-ff-web-1 \
    timeout 580 pnpm test --maxWorkers=1 > "$out/review-web-full.log" 2>&1
  tail -5 "$out/review-web-full.log"
elif [ "$1" = checks ]; then
  timeout 180 docker exec -e NODE_OPTIONS=--max-old-space-size=2048 jrc-p1b17-jrc-ff-web-1 \
    sh -c 'timeout 100 pnpm exec tsc --noEmit && timeout 60 pnpm lint' > "$out/review-web-checks.log" 2>&1
  timeout 60 docker exec jrc-p1b17-jrc-bb-web-1 \
    sh -c 'timeout 40 ruff check . && timeout 15 ruff format --check .' > "$out/review-bb-checks.log" 2>&1
elif [ "$1" = cleanup ]; then
  timeout 120 docker compose --project-name "$JRC_PROJECT" -f docker-compose.yml -f "$override" down -v
  for image in jrc-dev jrc-bb jrc-ff-api jrc-ff-web jrc-ingress jrc-s3; do
    docker image rm "$image:$JRC_PROJECT"
  done
fi
