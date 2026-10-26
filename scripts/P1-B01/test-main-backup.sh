#!/bin/sh
# Skutečná záloha z main před P1 → obnova aktuálním kódem; jen vlastní projekty.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b01 ] || exit 2
root=$PWD
. scripts/backup/test-lib.sh
new_run
revision=$(git rev-parse main)
snapshot=$(mktemp -d "$PWD/.cache/p1-main-XXXXXXXX")
src="jrc-p1b01-main-$(basename "$snapshot" | tr A-Z a-z)"
dst="${src}r"
require_unused_project "$src"
require_unused_project "$dst"
src_started=0 dst_started=0
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    set +e
    if [ "$src_started" = 1 ]; then
        cd "$snapshot/main" || exit 1
        record_images "$src"
        cleanup_project "$src"
    fi
    if [ "$dst_started" = 1 ]; then
        cd "$snapshot/new" || exit 1
        record_images "$dst"
        cleanup_project "$dst"
    fi
    cd "$root" || exit 1
    if [ "$result" = 0 ]; then
        rm -rf -- "$snapshot" "$run_dir"
    else
        printf 'Podklady neúspěšného testu: %s\n' "$snapshot" >&2
    fi
    exit "$result"
}
trap cleanup EXIT HUP INT TERM
# Místní klon sdílí jen čtení objektů gitu. main ani jeho pracovní složku nemění.
git clone --quiet --shared --single-branch --branch main /home/honza/jr-content "$snapshot/main"
[ "$(git -C "$snapshot/main" rev-parse HEAD)" = "$revision" ] || fail 'main se mezitím změnil'
printf 'Zdroj zálohy: main %s\n' "$revision"
mkdir "$snapshot/new"
tar --exclude=./.cache --exclude=./.git --exclude=./.env --exclude=./backups \
    --exclude=node_modules --exclude=.next --exclude=./ops/test-output --exclude=./ops/screenshoty \
    -cf - . | tar -xf - -C "$snapshot/new"
cd "$snapshot/main"
src_make() {
    make --no-print-directory JRC_PROJECT="$src" JRC_APP_PORT=13493 JRC_S3_PORT=14213 \
        JRC_POSTGRES_PORT=15743 JRC_BB_PORT=18313 "$@"
}
src_dc() { JRC_PROJECT="$src" checked_docker compose --project-name "$src" --file docker-compose.yml --env-file .env "$@"; }
src_started=1
src_make up
! grep -q '^JRC_MODE=' .env || fail 'Zdroj není před P1: obsahuje JRC_MODE'
src_dc exec -T jrc-bb-web python manage.py shell < scripts/backup/seed-bb.py > "$run_dir/seed-output"
tail -1 "$run_dir/seed-output" > "$snapshot/new/seed-bb.json"
chmod 600 "$snapshot/new/seed-bb.json"
JRC_APP_PORT=13493 JRC_S3_PORT=14213 seed_freeframe "$src" > "$run_dir/seed-ff-output"
tail -1 "$run_dir/seed-ff-output" > "$snapshot/new/seed-ff.json"
chmod 600 "$snapshot/new/seed-ff.json"
src_make backup
set -- "backups/$src/"*
[ "$#" = 1 ] && [ -f "$1/env.backup" ] || fail 'Chybí právě jedna záloha main'
backup=$1
! grep -q '^JRC_MODE=' "$backup/env.backup" || fail 'Stará záloha obsahuje JRC_MODE'
src_make backup-verify BACKUP="$backup"
mkdir -p "$snapshot/new/backups/$src"
cp -a "$backup" "$snapshot/new/backups/$src/"
# Uvolnit zdrojové porty před spuštěním prohlížeče nad obnoveným cílem.
src_make down
cd "$snapshot/new"
dst_make() {
    make --no-print-directory JRC_PROJECT="$dst" JRC_APP_PORT=13503 JRC_S3_PORT=14223 \
        JRC_POSTGRES_PORT=15753 JRC_BB_PORT=18323 JRC_PUBLIC_ORIGIN=http://127.0.0.1:13503 JRC_BASE_PATH=/jrc "$@"
}
[ ! -e .env ] || fail 'Cíl už má .env'
dst_started=1
dst_make restore BACKUP="$backup"
grep -qx 'JRC_MODE=prod' .env || fail 'make env nedoplnil JRC_MODE'
# První kontrola s existujícím novým .env musí také projít.
checked_docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
    --volume "$PWD:/workspace" --workdir /workspace/scripts/backup "jrc-dev:$dst" python3 -c '
from pathlib import Path
import jrc_backup as jb
saved = next(Path("../../backups").glob("*/*/env.backup"))
assert jb.compare_env(jb.load_env_file(Path("../../.env")), jb.load_env_file(saved)) == []
print("OK stará záloha i s existujícím novým .env; JRC_MODE není tajemství")'
checked_docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
    --env JRC_PUBLIC_ORIGIN=http://127.0.0.1:13503 --env JRC_BASE_PATH=/jrc --env JRC_APP_PORT=13503 --env JRC_S3_PORT=14223 \
    --volume "$PWD:/jrc" --workdir /jrc "jrc-dev:$dst" node scripts/backup/browser.cjs seed-bb.json restored-main.png
checked_docker run --rm --pull never --network host --user "$(id -u):$(id -g)" --env-file .env \
    --env JRC_PUBLIC_ORIGIN=http://127.0.0.1:13503 --env JRC_BASE_PATH=/jrc --env JRC_APP_PORT=13503 --env JRC_S3_PORT=14223 \
    --volume "$PWD:/jrc" --workdir /jrc "jrc-ff-api:$dst" python scripts/backup/check-ff.py seed-ff.json
mkdir -p "$root/ops/screenshoty/P1-B01-review2"
cp restored-main.png "$root/ops/screenshoty/P1-B01-review2/"
printf 'OK záloha main %s bez JRC_MODE obnovena novou verzí, přihlášení/obsah/obrázek/HLS ověřeny\n' "$revision"
