#!/bin/sh
# F7-B02: živé ověření schválení klientem. Jen projekt jrc-f7b02 (porty 13373/14093/15623/18193).
# Použití: JRC_PROJECT=jrc-f7b02 sh scripts/F7-B02/check-approval.sh   (po make up s těmito porty)
set -eu
[ "${JRC_PROJECT:-}" = "jrc-f7b02" ] || { echo 'Jen JRC_PROJECT=jrc-f7b02' >&2; exit 2; }
mkdir -p ops/test-output/F7-B02
COMPOSE="docker compose --project-name jrc-f7b02 --file docker-compose.yml --env-file .env"
$COMPOSE exec -T -e JRC_PROJECT=jrc-f7b02 jrc-bb-web python manage.py shell < scripts/F7-B02/seed.py \
  | tail -1 > ops/test-output/F7-B02/fixture.json
run_browser() {
  # Brightbean omezuje přihlášení z jedné adresy na 10 za minutu (počítadlo v paměti procesu); před během počkáme.
  sleep 65
  docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
    --env-file .env --env JRC_PROJECT=jrc-f7b02 --volume "$PWD:/jrc" --workdir /jrc \
    "jrc-dev:jrc-f7b02" node scripts/F7-B02/check-approval.cjs "$1"
}
# Fáze 1: do žádosti Elly o změny. Pak tým uloží novou verzi scénáře a pošle obsah znovu do Review.
run_browser 1
$COMPOSE exec -T -e JRC_PROJECT=jrc-f7b02 -e JRC_F7B02_STEP=rework jrc-bb-web python manage.py shell < scripts/F7-B02/seed.py > ops/test-output/F7-B02/rework.log
run_browser 2
POST=$(python3 -c "import json;print(json.load(open('ops/test-output/F7-B02/fixture.json'))['post'])")
$COMPOSE exec -T jrc-bb-web python manage.py shell -c "
from apps.approvals.models import ApprovalAction
from apps.composer.models import PlatformPost
rows = list(ApprovalAction.objects.filter(post_id='$POST').order_by('created_at').values_list('action', 'user__email', 'note_visibility'))
print(rows)
actions = [r[0] for r in rows]
assert actions == ['submitted', 'approved', 'changes_requested', 'submitted', 'approved', 'approved'], actions
assert rows[2][1].startswith('ella-') and rows[2][2] == 'external', rows[2]
assert rows[5][1].startswith('ella-'), rows[5]
assert set(PlatformPost.objects.filter(post_id='$POST').values_list('status', flat=True)) == {'approved'}
print('OK databáze: schválení a žádost o změny od Elly uložené s viditelností external')
"
LOGS=$($COMPOSE logs --no-color jrc-bb-web jrc-bb-worker jrc-ff-api jrc-proxy 2>&1 || true)
if printf '%s\n' "$LOGS" | grep -Eq 'v1\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'; then
  echo 'CHYBA: vstupenka v logu' >&2; exit 1
fi
echo 'OK: v logu není vstupenka'
