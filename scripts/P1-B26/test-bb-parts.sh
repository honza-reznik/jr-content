#!/bin/sh
# Celá sada Brightbean po částech (každá nejvýš 1200 s), jen v kontejneru projektu jrc-p1b26.
set -u
[ "${JRC_PROJECT:-}" = jrc-p1b26 ] || { echo 'Vyžadován JRC_PROJECT=jrc-p1b26' >&2; exit 2; }
cd "$(dirname "$0")/../.."
out=ops/test-output/P1-B26/bb
mkdir -p "$out"
: > "$out/summary.txt"
docker exec jrc-p1b26-jrc-bb-web-1 sh -c 'ruff check . && ruff format --check .' > "$out/ruff.log" 2>&1
echo "ruff exit=$?" >> "$out/summary.txt"
for part in "apps/jrc_production" "apps/jrc_video apps/jrc_thumbnails apps/jrc_texts apps/jrc_channels apps/jrc_storage apps/jrc_ui" \
  "apps/composer apps/publisher" "apps/accounts apps/analytics apps/api apps/api_keys apps/approvals apps/calendar" \
  "apps/client_portal apps/common apps/credentials apps/inbox apps/intelligence apps/mcp apps/media_library" \
  "apps/members apps/notifications apps/oauth_server apps/onboarding apps/organizations apps/settings_manager apps/social_accounts apps/workspaces" \
  "tests"; do
  name=$(echo "$part" | tr ' /' '__')
  start=$(date +%s)
  timeout 1200 docker exec jrc-p1b26-jrc-bb-web-1 timeout 1190 pytest -p no:cacheprovider -o addopts="" -q --tb=short \
    --ds=config.settings.jrc_test $part > "$out/$name.log" 2>&1
  code=$?
  echo "$part exit=$code seconds=$(( $(date +%s) - start )) $(tail -1 "$out/$name.log")" >> "$out/summary.txt"
done
cat "$out/summary.txt"
