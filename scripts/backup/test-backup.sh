#!/bin/sh
# make test-backup (F2-B09): vlastní vymyšlená data, záloha, obnova do jiného PRÁZDNÉHO projektu,
# porovnání (řádky, objekty a součty, svazek médií), přihlášení a zobrazení v Chromiu, úklid obou projektů.
# Hlavního projektu jrc se test nikdy nedotkne. Zdrojový projekt (JRC_PROJECT=jrc-…) musí být nový;
# už existující se použije jen s JRC_TEST_BACKUP_REUSE=1 (data se přidají a projekt se nesmaže).
set -eu

src=${JRC_PROJECT:?JRC_PROJECT chybí}
dst=${JRC_RESTORE_PROJECT:-${src}r}
dst_app=${JRC_RESTORE_APP_PORT:-13313}
dst_s3=${JRC_RESTORE_S3_PORT:-14033}
dst_pg=${JRC_RESTORE_POSTGRES_PORT:-15563}
dst_bb=${JRC_RESTORE_BB_PORT:-18133}
out=ops/test-output/F2-B09
shots=ops/screenshoty/F2-B09
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
env_port() { value=$(eval "printf '%s' \"\${$1:-}\""); [ -n "$value" ] || value=$(sed -n "s/^$1=//p" .env 2>/dev/null | head -n 1); printf '%s' "$value"; }
for name in JRC_APP_PORT JRC_S3_PORT JRC_POSTGRES_PORT JRC_BB_PORT; do
    case "$name" in JRC_APP_PORT) mine=$dst_app ;; JRC_S3_PORT) mine=$dst_s3 ;; JRC_POSTGRES_PORT) mine=$dst_pg ;; *) mine=$dst_bb ;; esac
    [ "$mine" != "$(env_port "$name")" ] || fail "Cílový projekt musí mít jiný $name než zdrojový ($mine)."
done

# JRC_PROJECT musí vždy odpovídat názvu projektu: z něj Compose odvozuje názvy svazků (jinak by down --volumes
# mířil na svazky jiného projektu).
dc() { proj=$1; shift; JRC_PROJECT="$proj" docker compose --project-name "$proj" --file docker-compose.yml --env-file .env "$@"; }
volumes_of() { docker volume ls --format '{{.Name}}' | grep -E "^$1-(postgres-data|s3-data|bb-media|redis-data|ff-web-modules|ff-web-next)$" || true; }

[ -z "$(volumes_of "$dst")" ] || fail "Cílový projekt $dst už má svazky; test do nich nezasahuje. Smaž je ručně (JRC_PROJECT=$dst docker compose --project-name $dst --file docker-compose.yml --env-file .env down --volumes)."
src_created=0
if [ -n "$(volumes_of "$src")" ]; then
    [ "${JRC_TEST_BACKUP_REUSE:-}" = 1 ] || fail "Projekt $src už existuje. Test do něj přidává data; povol to JRC_TEST_BACKUP_REUSE=1 (projekt se na konci nesmaže)."
fi
backups_existed=0
[ -d "backups/$src" ] && backups_existed=1
made_backups=""
dst_started=0

cleanup() {
    status=$?
    set +e
    rm -f "$out/seed-bb.json"
    if [ "${JRC_TEST_BACKUP_KEEP:-}" = 1 ]; then
        printf 'Úklid vynechán (JRC_TEST_BACKUP_KEEP=1): projekty %s a %s zůstávají.\n' "$src" "$dst"
        exit "$status"
    fi
    step "Úklid (jen projekty $src, $dst a jejich testovací zálohy)"
    if [ "$dst_started" = 1 ] || [ -n "$(volumes_of "$dst")" ]; then
        dc "$dst" down --volumes --remove-orphans >/dev/null 2>&1
    fi
    if [ "$src_created" = 1 ]; then
        dc "$src" down --volumes --remove-orphans >/dev/null 2>&1
        [ "$backups_existed" = 1 ] || rm -rf "backups/$src"
    else
        for directory in $made_backups; do rm -rf "backups/$src/$directory"; done
    fi
    rm -rf "backups/$src/nechat-byt"
    rmdir "backups/$src" 2>/dev/null
    exit "$status"
}
trap cleanup EXIT

mkdir -p "$out" "$shots"

step "Zdrojový projekt $src"
if [ -z "$(volumes_of "$src")" ]; then
    src_created=1
    make --no-print-directory up
fi
sh scripts/workspace.sh check

step 'Vymyšlená data: obsah, text, obrázek, video (FFmpeg), stará média'
seed_bb="$out/seed-bb.json"
dc "$src" exec -T jrc-bb-web python manage.py shell < scripts/backup/seed-bb.py | tail -n 1 > "$seed_bb"
chmod 600 "$seed_bb"
grep -q workspace_id "$seed_bb" || fail 'Seed Brightbean nevytvořil data.'
docker run --rm --pull never --network none --user 0:0 --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add CHOWN \
    --mount "type=volume,src=$src-bb-media,dst=/m" "jrc-dev:$src" \
    sh -c 'mkdir -p /m/legacy && printf "Stará média (testovací soubor)\n" > /m/legacy/stara-media.txt'
seed_ff="$out/seed-ff.json"
docker run --rm --network host --pull never --user "$uid:$gid" --env-file .env --volume "$PWD:/jrc" --workdir /jrc \
    "jrc-ff-api:$src" python scripts/backup/seed-ff.py | tail -n 1 > "$seed_ff"
grep -q source_sha256 "$seed_ff" || fail 'Seed FreeFrame nevytvořil video.'

inventory() { dc "$1" exec -T jrc-dev python3 "$tool" inventory; }
media_listing() {
    docker run --rm --pull never --network none --user 0:0 --cap-drop ALL --cap-add DAC_READ_SEARCH --cap-add DAC_OVERRIDE \
        --mount "type=volume,src=$1-bb-media,dst=/m,readonly" "jrc-dev:$src" \
        sh -c 'cd /m && find . -type f | sort | xargs -r sha256sum'
}

step 'Stav zdroje před zálohou'
inventory "$src" > "$out/inv-before.json"
media_listing "$src" > "$out/media-src.txt"

step 'Záloha (první)'
keep=1000
[ "$backups_existed" = 1 ] || keep=1
JRC_BACKUP_KEEP=1000 make --no-print-directory backup
first=$(ls -1 "backups/$src" | grep -E '^[0-9]{8}-[0-9]{6}$' | tail -n 1)
made_backups="$first"
mkdir -p "backups/$src/nechat-byt" && echo 'cizí složka bez manifestu' > "backups/$src/nechat-byt/poznamka.txt"

step "Záloha (druhá, JRC_BACKUP_KEEP=$keep) a rotace"
sleep 1
JRC_BACKUP_KEEP=$keep make --no-print-directory backup
latest=$(ls -1 "backups/$src" | grep -E '^[0-9]{8}-[0-9]{6}$' | tail -n 1)
[ "$latest" != "$first" ] || fail 'Druhá záloha nevznikla.'
made_backups="$first $latest"
[ -f "backups/$src/nechat-byt/poznamka.txt" ] || fail 'Rotace smazala adresář bez manifestu.'
if [ "$backups_existed" != 1 ]; then
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
restore
step 'Druhá obnova do téhož (už neprázdného) projektu se musí odmítnout'
if output=$(restore 2>&1); then fail 'Obnova do neprázdného projektu proběhla.'; fi
printf '%s' "$output" | grep -q 'už obsahuje data' || { printf '%s\n' "$output" | tail -5 >&2; fail 'Odmítnutí neprázdného cíle má jiný důvod.'; }
printf 'OK odmítnuto: cíl už obsahuje data\n'

step 'Porovnání zdroje a obnoveného projektu'
inventory "$dst" > "$out/inv-restored.json"
media_listing "$dst" > "$out/media-dst.txt"
docker run --rm --network none --pull never --user "$uid:$gid" --env PYTHONDONTWRITEBYTECODE=1 --volume "$PWD:/jrc" --workdir /jrc \
    "jrc-dev:$src" python3 scripts/backup/compare.py "$out/inv-before.json" "$out/inv-after.json" "$out/inv-restored.json" "$seed_ff" \
    "$out/media-src.txt" "$out/media-dst.txt"

step 'Chromium a FreeFrame v obnoveném projektu'
docker run --rm --network host --pull never --user "$uid:$gid" --env JRC_APP_PORT="$dst_app" --env JRC_S3_PORT="$dst_s3" \
    --volume "$PWD:/jrc" --workdir /jrc "jrc-dev:$dst" node scripts/backup/browser.cjs "$seed_bb" "$shots/obnoveno.png"
docker run --rm --network host --pull never --user "$uid:$gid" --env-file .env --env JRC_APP_PORT="$dst_app" --env JRC_S3_PORT="$dst_s3" \
    --volume "$PWD:/jrc" --workdir /jrc "jrc-ff-api:$dst" python scripts/backup/check-ff.py "$seed_ff"

printf '\nVšechny kontroly test-backup prošly.\n'
