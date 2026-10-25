#!/bin/sh
# F5-B08: živé ověření záložky Video v izolovaném projektu jrc-f5b08 nebo jrc-f5b09a.
# Použití: nastav JRC_PROJECT na jrc-f5b08 nebo jrc-f5b09a a spusť po make up.
set -eu
[ "${JRC_PROJECT:-}" = "jrc-f5b08" ] || [ "${JRC_PROJECT:-}" = "jrc-f5b09a" ] || { echo 'Jen ověřovací projekt jrc-f5b08 nebo jrc-f5b09a' >&2; exit 2; }
mkdir -p ops/test-output/F5-B08
project="$JRC_PROJECT"
COMPOSE="docker compose --project-name $project --file docker-compose.yml --env-file .env"
$COMPOSE exec -T -e JRC_PROJECT=$project jrc-bb-web python manage.py shell < scripts/F5-B08/seed.py \
  > ops/test-output/F5-B08/fixture.json
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT=$project --volume "$PWD:/jrc" --workdir /jrc \
  "jrc-dev:$project" node scripts/F5-B08/check-video-tab.cjs
POST=$(python3 -c "import json;print(json.load(open('ops/test-output/F5-B08/fixture.json'))['post'])")
# Uložení "Final for publishing" v Brightbeanu: jedna finální verze, kdo a kdy.
$COMPOSE exec -T jrc-bb-web python manage.py shell -c "
from apps.jrc_video.models import VideoAsset
video = VideoAsset.objects.get(platform_post__post_id='$POST')
assert video.final_version is not None and video.final_version.number == 2, video.final_version
assert video.final_by is not None and video.final_by.email.startswith('manager-') and video.final_at
print('OK databáze: finální verze v%d, označil %s' % (video.final_version.number, video.final_by.email))
"
# AK4: stav převodu se obnoví i bez otevřené záložky (worker, ~30 s). Verzi 1 vrátíme do stavu 'processing'.
$COMPOSE exec -T jrc-bb-web python manage.py shell -c "
from apps.jrc_video.models import VideoVersion
VideoVersion.objects.filter(video__platform_post__post_id='$POST', number=1).update(status='processing', width=None, height=None, duration_s=None)
"
i=0
while [ "$i" -lt 30 ]; do
  state=$($COMPOSE exec -T jrc-bb-web python manage.py shell -c "
from apps.jrc_video.models import VideoVersion
v = VideoVersion.objects.get(video__platform_post__post_id='$POST', number=1)
print(v.status, v.width, v.height)
" | tail -1)
  case "$state" in ready*) echo "OK: worker obnovil stav převodu bez otevřené záložky ($state)"; break;; esac
  i=$((i + 1)); sleep 5
done
[ "$i" -lt 30 ] || { echo "CHYBA: worker stav neobnovil ($state)" >&2; exit 1; }
# Caddy musí maskovat tiket v Location i citlivý parametr při přístupu i warningu.
LOGS=$($COMPOSE logs --no-color jrc-bb-web jrc-bb-worker jrc-ff-api jrc-proxy 2>&1 || true)
if printf '%s\n' "$LOGS" | grep -Eq 'v1\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|eyJhbGci'; then
  echo 'CHYBA: přístupový token nebo tiket v logu' >&2; exit 1
fi
echo 'OK: v logu není přístupový token ani tiket'
