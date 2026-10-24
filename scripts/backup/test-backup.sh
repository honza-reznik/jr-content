#!/bin/sh
# make test-backup (F2-B09): vlastní vymyšlená data, záloha, obnova do jiného PRÁZDNÉHO projektu,
# porovnání (řádky, objekty a součty, svazek médií), přihlášení a zobrazení v Chromiu, úklid obou projektů.
# JRC_PROJECT je jen prefix; oba skutečné projekty dostávají jedinečnou příponu tohoto běhu.
set -eu

base=${JRC_PROJECT:?JRC_PROJECT chybí}
[ "$base" != jrc ] || { printf 'Test pro jrc je zakázáno.\n' >&2; exit 1; }
. scripts/backup/test-lib.sh
new_run
export JRC_BACKUP_DOCKER_FAILURE_FILE="$run_dir/docker-failed"
src="${base}-${run_id}"
dst="${src}r"
export JRC_PROJECT="$src"
dst_app=${JRC_RESTORE_APP_PORT:-13313}
dst_s3=${JRC_RESTORE_S3_PORT:-14033}
dst_pg=${JRC_RESTORE_POSTGRES_PORT:-15563}
dst_bb=${JRC_RESTORE_BB_PORT:-18133}
out=ops/test-output/F2-B09/$run_id
shots=ops/screenshoty/F2-B09/$run_id
tool=scripts/backup/jrc_backup.py
uid=$(id -u)
gid=$(id -g)
fail() { printf 'CHYBA TESTU: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s ==\n' "$*"; }

for name in "$src" "$dst"; do
    case "$name" in jrc) fail 'Test záloh nesmí používat hlavní projekt jrc.' ;; esac
    printf '%s' "$name" | grep -Eqx 'jrc-[a-z0-9-]+' || fail "Neplatný název projektu: $name"
done
[ "$src" != "$dst" ] || fail 'Zdrojový a cílový projekt musí být různé.'
[ "$dst_app" != 13000 ] && [ "$dst_s3" != 13900 ] || fail 'Cílový projekt nesmí použít porty hlavního projektu.'
for name in JRC_APP_PORT JRC_S3_PORT JRC_POSTGRES_PORT JRC_BB_PORT; do
    case "$name" in JRC_APP_PORT) mine=$dst_app ;; JRC_S3_PORT) mine=$dst_s3 ;; JRC_POSTGRES_PORT) mine=$dst_pg ;; *) mine=$dst_bb ;; esac
    [ "$mine" != "$(env_port "$name")" ] || fail "Cílový projekt musí mít jiný $name než zdrojový ($mine)."
done

# JRC_PROJECT musí vždy odpovídat názvu projektu: z něj Compose odvozuje názvy svazků (jinak by down --volumes
# mířil na svazky jiného projektu).
dc() { proj=$1; shift; JRC_PROJECT="$proj" checked_docker compose --project-name "$proj" --file docker-compose.yml --env-file .env "$@"; }
require_unused_project "$src"
require_unused_project "$dst"
[ ! -L backups ] || fail 'backups nesmí být odkaz.'
src_created=0
dst_started=0
backups_owned=0
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] || [ -f "$run_dir/docker-failed" ]; then
        printf 'Neúspěšný test: prostředky ponechány k bezpečné kontrole bez dalšího volání Dockeru: %s, %s\n' "$src" "$dst" >&2
        [ "$status" -ne 0 ] || status=1
        exit "$status"
    fi
    if [ "${JRC_TEST_BACKUP_KEEP:-}" = 1 ]; then exit "$status"; fi
    # Kořen záloh vytvořil pouze tento běh; jiný kořen ani starší zálohy se nemažou.
    if [ "$backups_owned" = 1 ]; then
        [ ! -L backups ] && [ ! -L "backups/$src" ] || fail 'Odkaz při úklidu záloh.'
        checked_docker run --rm --pull never --network none --user "$uid:$gid" \
            --volume "$PWD:/workspace" --workdir /workspace "jrc-dev:$src" \
            python3 scripts/backup/cleanup-test.py "backups/$src"
    fi
    if [ "$dst_started" = 1 ]; then cleanup_project "$dst"; fi
    if [ "$src_created" = 1 ]; then cleanup_project "$src"; fi
    rm -f "${seed_bb:-$run_dir/nothing}"
    rm -rf "$run_dir"
    exit "$status"
}
trap cleanup EXIT

require_local_path "$out"
require_local_path "$shots"
mkdir -p "$out" "$shots"

step "Zdrojový projekt $src"
src_created=1
if make --no-print-directory up; then record_images "$src"; else
    : > "$run_dir/docker-failed"
    exit 1
fi
sh scripts/workspace.sh check

step 'Vymyšlená data: obsah, text, obrázek, video (FFmpeg), stará média'
seed_bb="$out/seed-bb.json"
dc "$src" exec -T jrc-bb-web python manage.py shell < scripts/backup/seed-bb.py > "$run_dir/seed-bb-output"
tail -n 1 "$run_dir/seed-bb-output" > "$seed_bb"
chmod 600 "$seed_bb"
grep -q workspace_id "$seed_bb" || fail 'Seed Brightbean nevytvořil data.'
checked_docker run --rm --pull never --network none --user 0:0 --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add CHOWN \
    --mount "type=volume,src=$src-bb-media,dst=/m" "jrc-dev:$src" \
    sh -c 'mkdir -p /m/legacy && printf "Stará média (testovací soubor)\n" > /m/legacy/stara-media.txt'
seed_ff="$out/seed-ff.json"
seed_freeframe "$src" > "$run_dir/seed-ff-output"
tail -n 1 "$run_dir/seed-ff-output" > "$seed_ff"
grep -q source_sha256 "$seed_ff" || fail 'Seed FreeFrame nevytvořil video.'

inventory() { dc "$1" exec -T jrc-dev python3 "$tool" inventory; }
media_listing() {
    checked_docker run --rm --pull never --network none --user 0:0 --cap-drop ALL --cap-add DAC_READ_SEARCH --cap-add DAC_OVERRIDE \
        --mount "type=volume,src=$1-bb-media,dst=/m,readonly" "jrc-dev:$src" \
        sh -c 'cd /m && find . -type f | sort | xargs -r sha256sum'
}

step 'Stav zdroje před zálohou'
inventory "$src" > "$out/inv-before.json"
media_listing "$src" > "$out/media-src.txt"

step 'Záloha (první)'
keep=1
# Výhradní vytvoření adresáře. Žádné přijetí existující složky.
mkdir -p backups
mkdir "backups/$src"
backups_owned=1
JRC_BACKUP_KEEP=1000 make --no-print-directory backup
first=$(ls -1 "backups/$src" | grep -E '^[0-9]{8}-[0-9]{6}$' | tail -n 1)
foreign=$(new_foreign_fixture "$src")

step "Záloha (druhá, JRC_BACKUP_KEEP=$keep) a rotace"
sleep 1
JRC_BACKUP_KEEP=$keep make --no-print-directory backup
latest=$(ls -1 "backups/$src" | grep -E '^[0-9]{8}-[0-9]{6}$' | tail -n 1)
[ "$latest" != "$first" ] || fail 'Druhá záloha nevznikla.'
[ -f "$foreign/poznamka.txt" ] || fail 'Rotace smazala adresář bez manifestu.'
if [ "$keep" = 1 ]; then
    [ ! -e "backups/$src/$first" ] || fail 'Rotace (KEEP=1) nesmazala starší zálohu.'
    printf 'OK rotace: nejstarší platná záloha odstraněna, cizí složka zůstala\n'
fi
inventory "$src" > "$out/inv-after.json"

step 'Kontrola zálohy'
make --no-print-directory backup-verify BACKUP="backups/$src/$latest"
[ "$(stat -c %a "backups/$src/$latest")" = 700 ] && [ "$(stat -c %a "backups/$src/$latest/env.backup")" = 600 ] \
    || fail 'Práva zálohy nejsou 700/600.'
git check-ignore -q "backups/$src/$latest/env.backup" || fail 'backups/ není v .gitignore.'
[ -z "$(git ls-files backups)" ] || fail 'Do gitu se dostal soubor ze složky backups.'
printf 'OK práva 700/600, backups/ mimo git\n'

step "Obnova do prázdného projektu $dst"
restore() {
    env JRC_PROJECT="$dst" JRC_APP_PORT="$dst_app" JRC_S3_PORT="$dst_s3" JRC_POSTGRES_PORT="$dst_pg" JRC_BB_PORT="$dst_bb" \
        make --no-print-directory restore BACKUP="backups/$src/$latest"
}
dst_started=1
if restore; then :; else
    : > "$run_dir/docker-failed"
    exit 1
fi
step 'Druhá obnova do téhož (už neprázdného) projektu se musí odmítnout'
if output=$(restore 2>&1); then fail 'Obnova do neprázdného projektu proběhla.'; fi
printf '%s' "$output" | grep -q 'už obsahuje data' || { printf '%s\n' "$output" | tail -5 >&2; fail 'Odmítnutí neprázdného cíle má jiný důvod.'; }
printf 'OK odmítnuto: cíl už obsahuje data\n'
# I odmítnuté make restore sestaví náš vývojový obraz; evidujeme ID až po posledním sestavení.
record_images "$dst"

step 'Porovnání zdroje a obnoveného projektu'
inventory "$dst" > "$out/inv-restored.json"
media_listing "$dst" > "$out/media-dst.txt"
checked_docker run --rm --network none --pull never --user "$uid:$gid" --env PYTHONDONTWRITEBYTECODE=1 --volume "$PWD:/jrc" --workdir /jrc \
    "jrc-dev:$src" python3 scripts/backup/compare.py "$out/inv-before.json" "$out/inv-after.json" "$out/inv-restored.json" "$seed_ff" \
    "$out/media-src.txt" "$out/media-dst.txt"

step 'Chromium a FreeFrame v obnoveném projektu'
checked_docker run --rm --network host --pull never --user "$uid:$gid" --env JRC_APP_PORT="$dst_app" --env JRC_S3_PORT="$dst_s3" \
    --volume "$PWD:/jrc" --workdir /jrc "jrc-dev:$dst" node scripts/backup/browser.cjs "$seed_bb" "$shots/obnoveno.png"
checked_docker run --rm --network host --pull never --user "$uid:$gid" --env-file .env --env JRC_APP_PORT="$dst_app" --env JRC_S3_PORT="$dst_s3" \
    --volume "$PWD:/jrc" --workdir /jrc "jrc-ff-api:$dst" python scripts/backup/check-ff.py "$seed_ff"

printf '\nVšechny kontroly test-backup prošly.\n'
