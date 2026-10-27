#!/bin/sh
# P1-B04: řízení Dockeru na hostiteli; age i git běží pouze v jrc-dev.
set -eu

project=${JRC_PROJECT:?JRC_PROJECT chybí}
workdir=$(pwd -P)
dev_image="jrc-dev:$project"
operation=${1:-}
case "$project" in jrc | jrc-?*) ;; *) echo 'Neplatný JRC_PROJECT.' >&2; exit 2 ;; esac

die() { printf 'CHYBA: %s\n' "$*" >&2; exit 1; }
config_value() {
    key=$1
    sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" .env | tail -n 1
}
repo=$(config_value JRC_BACKUP_GIT_DIR)
[ -n "$repo" ] || repo=/home/honza/jr-content-backup-git
case "$repo" in /*) ;; *) repo="$workdir/$repo" ;; esac
[ ! -L "$repo" ] || die 'JRC_BACKUP_GIT_DIR nesmí být symbolický odkaz.'
mkdir -p "$repo"
age_recipient=$(config_value JRC_BACKUP_AGE_RECIPIENT)
keep=$(config_value JRC_BACKUP_KEEP_OFFSITE)
[ -n "$keep" ] || keep=14
case "$keep" in *[!0-9]*|'') die 'JRC_BACKUP_KEEP_OFFSITE musí být celé kladné číslo.' ;; esac
[ "$keep" -ge 1 ] || die 'JRC_BACKUP_KEEP_OFFSITE musí být alespoň 1.'

case "$operation" in
    backup)
        [ -n "$age_recipient" ] || die 'Doplň JRC_BACKUP_AGE_RECIPIENT do .env.'
        # Vytvoření i ověření místní zálohy proběhne přes stávající F2-B09 příkazy.
        make --no-print-directory JRC_PROJECT="$project" backup
        latest=$(find "backups/$project" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*' -printf '%f\n' | sort | tail -n 1)
        [ -n "$latest" ] || die 'Nevznikla místní záloha.'
        make --no-print-directory JRC_PROJECT="$project" backup-verify BACKUP="backups/$project/$latest"
        docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
            --env-file .env --env JRC_OFFSITE_OPERATION=backup --env JRC_OFFSITE_BACKUP="/workspace/backups/$project/$latest" \
            --env JRC_BACKUP_GIT_DIR=/offsite --env JRC_BACKUP_KEEP_OFFSITE="$keep" \
            --volume "$workdir:/workspace" --volume "$repo:/offsite" --workdir /workspace \
            "$dev_image" python3 scripts/backup/offsite.py backup
        ;;
    restore)
        backup=${2:-}
        keyfile=${3:-}
        [ -n "$backup" ] || die 'Použití: make restore-offsite BACKUP=<čas nebo soubor.age> AGE_KEY_FILE=<soukromý klíč>'
        [ -n "$keyfile" ] || die 'Chybí AGE_KEY_FILE=<cesta k soukromému klíči age>.'
        [ -f "$keyfile" ] && [ ! -L "$keyfile" ] || die 'Soubor AGE_KEY_FILE neexistuje nebo je odkaz.'
        case "$backup" in */*|*.age) archive=$backup ;; *) archive="${backup}.age" ;; esac
        case "$archive" in /*) ;; *) archive="$repo/$archive" ;; esac
        [ -f "$archive" ] && [ ! -L "$archive" ] || die "Šifrovaná záloha neexistuje: $archive"
        docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
            --env-file .env --env JRC_OFFSITE_OPERATION=restore --env JRC_OFFSITE_ARCHIVE=/offsite/"$(basename "$archive")" \
            --env JRC_OFFSITE_OUTPUT="/workspace/backups/$project" \
            --volume "$workdir:/workspace" --volume "$(dirname "$archive"):/offsite:ro" \
            --volume "$keyfile:/age-key:ro" --workdir /workspace \
            "$dev_image" python3 scripts/backup/offsite.py restore --identity /age-key
        echo "Obnovená databázová záloha je v backups/$project; pokračuj: JRC_PROJECT=$project make restore BACKUP=backups/$project/<čas>"
        ;;
    *) die 'Neznámá operace offsite.' ;;
esac
