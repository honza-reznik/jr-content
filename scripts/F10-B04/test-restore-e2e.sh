#!/bin/sh
# E2E Reel pro Ellu → záloha A → obnova do prázdného B → Chromium; vlastní projekty F10-B04.
set -eu
src=${JRC_PROJECT:-}
case "$src" in jrc-[a-z0-9-]*) ;; *) echo 'Spusť s JRC_PROJECT=jrc-<název> make test-restore-e2e.' >&2; exit 2 ;; esac
[ "$src" != jrc ] || exit 2
. scripts/backup/test-lib.sh
new_run
dst=${src}r
src_app=13473 src_s3=14193 src_pg=15723 src_bb=18293
dst_app=13483 dst_s3=14203 dst_pg=15733 dst_bb=18303
backup_root=backups/$src
output=ops/test-output/F10-B04/$run_id
shots=ops/screenshoty/F10-B04/$run_id
uid=$(id -u) gid=$(id -g)
env_existed=0
[ -e .env ] && env_existed=1
src_started=0 dst_started=0 backups_owned=0 output_owned=0
backup_path=
fail() { printf 'CHYBA F10-B04: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s ==\n' "$*"; }
dc() { project=$1; shift; JRC_PROJECT="$project" checked_docker compose --project-name "$project" --file docker-compose.yml --env-file .env "$@"; }
check_restored_comment() {
    project=$1
    count=$(JRC_PROJECT="$project" checked_docker compose --project-name "$project" --file docker-compose.yml --env-file .env \
        exec -T jrc-postgres psql -U jrc -d freeframe -Atc "SELECT count(*) FROM comments WHERE body = 'zkrátit' AND timecode_start BETWEEN 17 AND 18")
    [ "$count" -gt 0 ] || fail "Komentář klientky u 0:17 se v projektu $project neobnovil."
    printf 'OK komentář klientky u 0:17 je v obnovené databázi (%s záznamů)\n' "$count"
}
check_media() {
    project=$1 service=$2
    JRC_PROJECT="$project" checked_docker compose --project-name "$project" --file docker-compose.yml --env-file .env exec -T \
        -e JRC_EXPECT_UID="$uid" -e JRC_EXPECT_GID="$gid" "$service" python -c '
import os
from pathlib import Path
root = Path("/app/media")
st = root.stat()
expected = (int(os.environ["JRC_EXPECT_UID"]), int(os.environ["JRC_EXPECT_GID"]))
actual = (st.st_uid, st.st_gid)
assert actual == expected, f"{root} vlastní {actual}, čekáno {expected}"
p = root / "jrc-smoke" / "f2-b09b-owner-check"
p.parent.mkdir(exist_ok=True)
p.write_text("ok", encoding="utf-8")
assert p.read_text(encoding="utf-8") == "ok"
p.unlink()
p.parent.rmdir()
print(f"OK {root}: vlastník {actual}, zápis/čtení/smazání funguje")'
}
make_source() {
    make --no-print-directory JRC_PROJECT="$src" JRC_APP_PORT="$src_app" JRC_S3_PORT="$src_s3" \
        JRC_POSTGRES_PORT="$src_pg" JRC_BB_PORT="$src_bb" "$@"
}
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    set +e
    # Každá Compose operace má shodné JRC_PROJECT a --project-name a míří jen na A nebo B.
    if [ "$dst_started" = 1 ]; then cleanup_project "$dst"; fi
    if [ "$src_started" = 1 ]; then cleanup_project "$src"; fi
    if [ "$backups_owned" = 1 ] && [ -d "$backup_root" ] && [ ! -L "$backup_root" ]; then
        # Adresář projektu vznikl jen v tomto běhu; odstraníme jen ověřenou jedinou zálohu.
        for item in "$backup_root"/*; do
            [ -e "$item" ] || continue
            [ "$item" = "$backup_path" ] && [ ! -L "$item" ] || { echo "Neznámý obsah v $backup_root; zálohy ponechány." >&2; status=1; continue; }
            rm -rf -- "$item"
        done
        rmdir "$backup_root" 2>/dev/null || true
    fi
    [ "$output_owned" = 0 ] || rm -rf -- "$output"
    [ "$env_existed" = 1 ] || rm -f .env
    rm -rf -- "$run_dir"
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

require_unused_project "$src"
require_unused_project "$dst"
for port in "$src_app" "$src_s3" "$src_pg" "$src_bb" "$dst_app" "$dst_s3" "$dst_pg" "$dst_bb"; do
    case "$port" in 13000|13900|18011|13473|14193|15723|18293|13483|14203|15733|18303) ;; *) fail "Neočekávaný port $port" ;; esac
done
require_local_path "$output"
require_local_path "$shots"
mkdir -p "$output" "$shots"
output_owned=1

step "Zdrojový projekt $src a E2E Reel pro Ellu"
if [ "$env_existed" = 1 ]; then fail 'Worktree už obsahuje .env; test očekává čisté prostředí, aby mohl bezpečně odstranit svůj dočasný soubor.'; fi
src_started=1
if make_source up; then record_images "$src"; else record_images "$src"; exit 1; fi
check_media "$src" jrc-bb-web
check_media "$src" jrc-bb-worker
sh scripts/workspace.sh check
dc "$src" exec -T -e JRC_PROJECT="$src" -e JRC_E2E_PROJECT="$src" jrc-bb-web python manage.py shell \
    < scripts/F10-B02/seed.py > "$run_dir/seed-output"
tail -n 1 "$run_dir/seed-output" > "$output/fixture.json"
chmod 600 "$output/fixture.json"
grep -q '"workspace"' "$output/fixture.json" || fail 'E2E seed nevytvořil testovací tým a Ellu.'
JRC_PROJECT="$src" dc "$src" exec -T jrc-ff-api python /jrc-scripts/F10-B02/check-cors.py
JRC_PROJECT="$src" JRC_E2E_PROJECT="$src" JRC_APP_PORT="$src_app" JRC_S3_PORT="$src_s3" \
    JRC_E2E_APP_PORT="$src_app" JRC_E2E_S3_PORT="$src_s3" \
    JRC_E2E_OUTPUT="$output" JRC_E2E_SCREENSHOTS="$shots" \
    checked_docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
      --env-file .env --env JRC_PROJECT="$src" --env JRC_APP_PORT="$src_app" --env JRC_S3_PORT="$src_s3" \
      --env JRC_E2E_PROJECT="$src" --env JRC_E2E_APP_PORT="$src_app" --env JRC_E2E_S3_PORT="$src_s3" \
      --env JRC_E2E_OUTPUT="$output" --env JRC_E2E_SCREENSHOTS="$shots" \
      --volume "$PWD:/jrc" --workdir /jrc "jrc-dev:$src" node scripts/F10-B02/reel-for-ella.cjs

step 'Záloha a její úplná kontrola'
mkdir -p backups
[ ! -L backups ] || fail 'Kořen backups je symbolický odkaz.'
mkdir "$backup_root" || fail "Nelze vyhradit záložní složku $backup_root."
backups_owned=1
make_source backup
set -- "$backup_root"/*
[ "$#" -eq 1 ] && [ -d "$1" ] && [ ! -L "$1" ] || fail 'V záložní složce není právě jedna nová záloha.'
backup_path=$1
case "$backup_path" in "$backup_root"/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) fail 'Nečekaný název testovací zálohy.' ;; esac
make_source backup-verify BACKUP="$backup_path"

step "Obnova do čistého projektu $dst"
dst_started=1
if make --no-print-directory JRC_PROJECT="$dst" JRC_APP_PORT="$dst_app" JRC_S3_PORT="$dst_s3" \
    JRC_POSTGRES_PORT="$dst_pg" JRC_BB_PORT="$dst_bb" restore BACKUP="$backup_path"; then
    record_images "$dst"
else
    record_images "$dst"
    exit 1
fi
check_media "$dst" jrc-bb-web
check_media "$dst" jrc-bb-worker
check_restored_comment "$dst"

step 'Ověření obnovených dat v Chromiu'
checked_docker run --rm --pull never --network host --user "$uid:$gid" \
    --env JRC_PROJECT="$dst" --env JRC_EXPECTED_PROJECT="$dst" --env JRC_APP_PORT="$dst_app" --env JRC_S3_PORT="$dst_s3" \
    --volume "$PWD:/jrc" --workdir /jrc "jrc-dev:$dst" \
    node scripts/F10-B04/restore-browser.cjs "$output/fixture.json" "$shots/obnoveno.png"

step "Opakovaná kontrola vlastnictví zdroje $src po obnově projektu B"
check_media "$src" jrc-bb-web
check_media "$src" jrc-bb-worker

step "Celá sada make test na projektu $src"
make_source test

printf '\nVšechny kontroly F10-B04 prošly; uklízí se pouze projekty A/B, jejich obrazy a testovací záloha.\n'
