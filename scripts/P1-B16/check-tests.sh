#!/bin/sh
# Cílené regrese, výhradně uvnitř běžícího vlastního Docker projektu.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b16 ] || exit 2
mkdir -p ops/test-output/P1-B16
timeout 180 docker exec jrc-p1b16-jrc-ff-api-1 timeout 170 python -m pytest \
  apps/api/tests/test_database_recovery.py apps/api/tests/test_processing_lifecycle.py \
  apps/api/tests/test_presigned_url_redaction.py apps/api/tests/test_transcode_refuses_a_short_result.py \
  -q -p no:cacheprovider > ops/test-output/P1-B16/ff-targeted.log 2>&1
timeout 240 docker exec jrc-p1b16-jrc-bb-web-1 timeout 230 pytest \
  -p no:cacheprovider -o addopts= -q --tb=short --ds=config.settings.jrc_test \
  apps/members/tests/test_invitation_expiry_display.py apps/members/tests/test_invite_throttle.py \
  apps/jrc_thumbnails/tests apps/jrc_channels/tests > ops/test-output/P1-B16/bb-targeted.log 2>&1
timeout 30 docker exec jrc-p1b16-jrc-bb-web-1 ruff check apps/members/tests/test_invitation_expiry_display.py
cat ops/test-output/P1-B16/ff-targeted.log ops/test-output/P1-B16/bb-targeted.log
