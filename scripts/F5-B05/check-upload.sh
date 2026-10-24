#!/bin/sh
# F5-B05: živé ověření nahrání verze videa. Jen projekt jrc-f5b05 (porty 13333/14053/15583/18153).
# Použití: JRC_PROJECT=jrc-f5b05 sh scripts/F5-B05/check-upload.sh   (po make up s těmito porty)
set -eu
[ "${JRC_PROJECT:-}" = "jrc-f5b05" ] || { echo 'Jen JRC_PROJECT=jrc-f5b05' >&2; exit 2; }
mkdir -p ops/test-output/F5-B05
COMPOSE="docker compose --project-name jrc-f5b05 --file docker-compose.yml --env-file .env"
# Nový obsah při každém spuštění (jeden nedokončený upload na kanál a uživatele).
$COMPOSE exec -T -e JRC_PROJECT=jrc-f5b05 jrc-bb-web python manage.py shell < scripts/F5-B05/seed.py \
  > ops/test-output/F5-B05/fixture.json
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT=jrc-f5b05 --volume "$PWD:/jrc" --workdir /jrc \
  "jrc-dev:jrc-f5b05" node scripts/F5-B05/check-upload.cjs
# Databáze: jedno video na kanál, dvě verze s čísly 1 a 2, obě hotové.
POST=$(python3 -c "import json;print(json.load(open('ops/test-output/F5-B05/fixture.json'))['post'])")
$COMPOSE exec -T jrc-bb-web python manage.py shell -c "
from apps.jrc_video.models import VideoAsset, VideoVersion
assets = VideoAsset.objects.filter(platform_post__post_id='$POST')
assert assets.count() == 1, assets.count()
rows = list(VideoVersion.objects.filter(video__in=assets).order_by('number').values_list('number', 'status'))
assert rows == [(1, 'ready'), (2, 'ready')], rows
print('OK databáze:', rows)
"
# V logu nesmí být vstupenka ani token FreeFrame.
if $COMPOSE logs --no-color jrc-bb-web jrc-ff-api jrc-proxy 2>&1 | grep -Eq 'v1\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|eyJhbGci'; then
  echo 'CHYBA: vstupenka nebo token v logu' >&2; exit 1
fi
echo 'OK: v logu není vstupenka ani token'
