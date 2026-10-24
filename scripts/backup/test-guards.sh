#!/bin/sh
# Pojistky obnovy na úrovni příkazů (F2-B09): bez běžících služeb, nic z hlavního projektu se nedotkne.
# Každý běh má vlastní jedinečný projekt a svazek; uklízí se jen po úspěšném dokončení.
set -eu
base=${JRC_PROJECT:-jrc}
. scripts/backup/test-lib.sh
new_run
export JRC_BACKUP_DOCKER_FAILURE_FILE="$run_dir/docker-failed"
guard_project="jrc-${run_id}-guard"
require_unused_project "$guard_project"
[ ! -L backups ] || fail 'backups nesmí být odkaz.'
mkdir -p backups
fixture_root="backups/$guard_project"
mkdir "$fixture_root"
fixture="$fixture_root/zaloha"
mkdir "$fixture"
printf '{}' > "$fixture/manifest.json"
guard_volume=$guard_project-s3-data
guard_image=jrc-dev:$guard_project
volume_created=0
image_created=0
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] || [ -f "$run_dir/docker-failed" ]; then exit 1; fi
    if [ "$volume_created" = 1 ]; then
        label=$(checked_docker volume inspect --format '{{index .Labels "jrc.backup-test"}}' "$guard_volume")
        [ "$label" = "$run_id" ] || fail 'Nedoložené vlastnictví svazku.'
        checked_docker volume rm "$guard_volume" >/dev/null
    fi
    if [ "$image_created" = 1 ]; then
        current=$(checked_docker image inspect --format '{{.Id}}' "$guard_image")
        [ "$current" = "$image_id" ] || fail 'Obraz se změnil.'
        checked_docker image rm "$guard_image" >/dev/null
    fi
    # Složky vytvořené výhradně tímto během; rm není rekurzivní.
    rm "$fixture/manifest.json"
    rmdir "$fixture" "$fixture_root/bez-manifestu" "$fixture_root"
    rm -rf "$run_dir"
    exit "$status"
}
trap cleanup EXIT

fail() { printf 'CHYBA TESTU: %s\n' "$*" >&2; exit 1; }
expect_refused() {  # popis, očekávaný text, příkaz...
    description=$1; pattern=$2; shift 2
    if output=$("$@" 2>&1); then fail "$description: příkaz neměl uspět"; fi
    printf '%s' "$output" | grep -q -- "$pattern" || { printf '%s\n' "$output" >&2; fail "$description: chybí text '$pattern'"; }
    printf 'OK pojistka: %s\n' "$description"
}

expect_refused 'make restore pro projekt jrc' 'zakázáno' env JRC_PROJECT=jrc make --no-print-directory restore BACKUP="$fixture"
expect_refused 'make test-backup pro projekt jrc' 'zakázáno' env JRC_PROJECT=jrc make --no-print-directory test-backup
expect_refused 'restore-guard pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-guard "$fixture"
expect_refused 'restore-precheck pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-precheck "$fixture"
expect_refused 'restore-data pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-data "$fixture"
expect_refused 'restore-finish pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-finish
expect_refused 'obnova bez BACKUP' 'Chybí BACKUP' env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-guard ''
expect_refused 'obnova z neexistující zálohy' 'neexistuje' env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-guard "$fixture_root/nic"
mkdir "$fixture_root/bez-manifestu"
expect_refused 'obnova z adresáře bez manifestu' 'manifest' env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-guard "$fixture_root/bez-manifestu"
expect_refused 'neplatný název projektu' 'Neplatný' env 'JRC_PROJECT=jrc;x' sh scripts/backup/backup.sh restore-guard "$fixture"
expect_refused 'JRC_BACKUP_KEEP=0' 'JRC_BACKUP_KEEP' env JRC_PROJECT="$guard_project" JRC_BACKUP_KEEP=0 sh scripts/backup/backup.sh backup

# Neprázdný svazek cílového projektu: obnova se odmítne ještě před spuštěním čehokoli.
checked_docker image inspect "jrc-dev:$base" --format '{{.Id}}' >/dev/null
checked_docker tag "jrc-dev:$base" "$guard_image"
image_id=$(checked_docker image inspect --format '{{.Id}}' "$guard_image")
image_created=1
checked_docker volume create --label "jrc.backup-test=$run_id" "$guard_volume" >/dev/null
volume_created=1
checked_docker run --rm --pull never --network none --user 0:0 --mount "type=volume,src=$guard_volume,dst=/v" "$guard_image" sh -c 'echo data > /v/neco'
expect_refused 'neprázdný svazek cíle' 'už obsahuje data' env JRC_PROJECT=$guard_project sh scripts/backup/backup.sh restore-precheck "$fixture"
checked_docker run --rm --pull never --network none --user 0:0 --mount "type=volume,src=$guard_volume,dst=/v" "$guard_image" sh -c 'rm /v/neco; echo /x > /v/.jrc-working-dir'
env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-volume-check
printf 'OK pojistka: svazek jen s .jrc-working-dir je prázdný, kontrola nic nespouští ani nezapisuje\n'
echo 'Všechny pojistky obnovy fungují.'
