#!/bin/sh
# Zálohy a obnova JR Content (F2-B09): řízení Dockeru na hostiteli. Vlastní práce běží v Dockeru
# (scripts/backup/jrc_backup.py). Volá se z Makefile: make backup | backup-verify | restore.
# Záloha jen čte. Obnova nikdy nesmí zasáhnout projekt `jrc` ani svazky, ve kterých už něco je.
set -eu

project=${JRC_PROJECT:-jrc}
workdir=$(pwd -P)
dev_image="jrc-dev:$project"
tool=scripts/backup/jrc_backup.py

die() {
    printf 'CHYBA: %s\n' "$*" >&2
    exit 1
}

case "$project" in
    jrc | jrc-?*) ;;
    *) die "Neplatný projekt JR Content: $project" ;;
esac
printf '%s' "$project" | grep -Eqx 'jrc(-[a-z0-9-]+)?' || die "Neplatný název projektu: $project"

compose() {
    docker compose --project-name "$project" --file docker-compose.yml --env-file .env "$@"
}

need_image() {
    docker image inspect "$dev_image" >/dev/null 2>&1 || die "Chybí obraz $dev_image (spusť make build v tomto projektu)."
}

# Absolutní cesta k záloze; obsah se nikdy nemění (připojuje se jen pro čtení).
backup_path() {
    [ -n "${1:-}" ] || die 'Chybí BACKUP=<cesta k záloze>, např. make restore BACKUP=backups/jrc/20261024-120000'
    [ -d "$1" ] || die "Záloha $1 neexistuje."
    [ -f "$1/manifest.json" ] || die "V $1 chybí manifest.json; není to hotová záloha."
    (cd "$1" && pwd -P)
}

run_tool_isolated() {
    # Bez sítě: kontroly souborů zálohy nepotřebují službu ani internet.
    abs=$1
    shift
    docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
        --env PYTHONDONTWRITEBYTECODE=1 \
        --env JRC_APP_PORT --env JRC_POSTGRES_PORT --env JRC_S3_PORT --env JRC_BB_PORT \
        --volume "$workdir:/workspace" --volume "$abs:/backup:ro" --workdir /workspace \
        "$dev_image" python3 "$tool" "$@"
}

volume_exists() {
    docker volume inspect "$1" >/dev/null 2>&1
}

# Svazek smí neexistovat, nebo v něm smí být nanejvýš .jrc-working-dir.
require_empty_volume() {
    volume=$1
    volume_exists "$volume" || return 0
    content=$(docker run --rm --pull never --network none --user 0:0 --cap-drop ALL --cap-add DAC_READ_SEARCH \
        --mount "type=volume,src=$volume,dst=/v,readonly" "$dev_image" \
        sh -c 'find /v -mindepth 1 -not -name .jrc-working-dir | head -n 3') || die "Nelze zkontrolovat svazek $volume."
    if [ -n "$content" ]; then
        printf 'CHYBA: Svazek %s už obsahuje data. Obnova se provádí jen do PRÁZDNÉHO projektu.\n' "$volume" >&2
        printf 'Chceš-li ověřovací projekt %s začít znovu (smaže JEN jeho data):\n' "$project" >&2
        printf '  docker compose --project-name %s --file docker-compose.yml down --volumes\n' "$project" >&2
        exit 1
    fi
}

restore_guard() {
    case "$project" in
        jrc) die 'Obnova do hlavního projektu jrc je zakázaná (pojistka proti přepsání hlavních dat). Použij prázdný ověřovací projekt, např. JRC_PROJECT=jrc-obnova.' ;;
        jrc-?*) ;;
        *) die 'Cílem obnovy musí být ověřovací projekt jrc-<název>.' ;;
    esac
}

case "${1:?Chybí operace}" in
    backup)
        keep=${JRC_BACKUP_KEEP:-7}
        case "$keep" in '' | *[!0-9]* | 0 | 0[0-9]*) die 'JRC_BACKUP_KEEP musí být celé číslo alespoň 1.' ;; esac
        sh scripts/workspace.sh check
        need_image
        rev=$(git rev-parse HEAD 2>/dev/null || printf unknown)
        dirty=no
        if [ -n "$(git status --porcelain 2>/dev/null | head -n 1)" ]; then dirty=yes; fi
        # Běžící prostředí se jen čte; Garage musí obsahovat jen buckety, které umíme zálohovat.
        buckets=$(compose exec -T jrc-s3 garage bucket list | awk 'NR > 1 { print $3 }' | tr '\n' ' ') \
            || die 'Nelze zjistit seznam bucketů Garage (běží jrc-s3?).'
        stamp=$(date +%Y%m%d-%H%M%S)
        while [ -e "backups/$project/$stamp" ] || [ -e "backups/$project/$stamp.partial" ]; do
            sleep 1
            stamp=$(date +%Y%m%d-%H%M%S)
        done
        partial="backups/$project/$stamp.partial"
        cleanup_note() {
            status=$?
            if [ "$status" -ne 0 ] && [ -d "$partial" ]; then
                printf 'Záloha selhala. Rozpracovaný adresář %s zůstal (nic jiného se nemazalo); po kontrole ho smaž ručně.\n' "$partial" >&2
            fi
        }
        trap cleanup_note EXIT
        compose exec -T jrc-dev python3 "$tool" start --project "$project" --stamp "$stamp" --keep "$keep" --buckets "$buckets"
        media=absent
        if volume_exists "$project-bb-media"; then
            docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
                --mount "type=volume,src=$project-bb-media,dst=/media,readonly" \
                --mount "type=bind,src=$workdir/$partial,dst=/out" \
                "$dev_image" tar -cf /out/bb-media.tar -C /media .
            media=present
            printf '  svazek %s-bb-media: zálohován\n' "$project"
        fi
        no_env=
        if [ "${JRC_BACKUP_NO_ENV:-}" = 1 ]; then no_env=--no-env; fi
        # shellcheck disable=SC2086
        compose exec -T jrc-dev python3 "$tool" finish --project "$project" --stamp "$stamp" --keep "$keep" \
            --media "$media" --git-rev "$rev" --git-dirty "$dirty" $no_env
        printf 'Záloha %s/%s je uložená v %s/backups/%s/%s\n' "$project" "$stamp" "$workdir" "$project" "$stamp"
        ;;
    verify)
        need_image
        abs=$(backup_path "${2:-}")
        run_tool_isolated "$abs" verify --backup /backup
        ;;
    restore-guard)
        restore_guard
        backup_path "${2:-}" >/dev/null
        ;;
    restore-precheck)
        restore_guard
        need_image
        abs=$(backup_path "${2:-}")
        for name in postgres-data s3-data bb-media redis-data; do
            require_empty_volume "$project-$name"
        done
        run_tool_isolated "$abs" precheck --project "$project" --backup /backup
        ;;
    restore-data)
        restore_guard
        need_image
        abs=$(backup_path "${2:-}")
        # Úplná kontrola zálohy znovu těsně před zápisem (nic se zatím nespustilo).
        run_tool_isolated "$abs" precheck --project "$project" --backup /backup
        for name in postgres-data s3-data bb-media redis-data; do
            require_empty_volume "$project-$name"
        done
        # Médií ze starého svazku se nejdřív zbavujeme starostí: svazek vznikne prázdný, naplní se z tar.
        if [ -f "$abs/bb-media.tar" ]; then
            volume_exists "$project-bb-media" || docker volume create \
                --label "com.docker.compose.project=$project" --label com.docker.compose.volume=jrc-bb-media \
                "$project-bb-media" >/dev/null
            docker run --rm --pull never --network none --user 0:0 \
                --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
                --mount "type=volume,src=$project-bb-media,dst=/media" \
                --mount "type=bind,src=$abs,dst=/backup,readonly" \
                "$dev_image" sh -c \
                'test -z "$(ls -A /media)" || exit 3; tar -xf /backup/bb-media.tar -C /media --no-same-owner && chown -R "$1:$2" /media' \
                sh "$(id -u)" "$(id -g)"
            printf 'Svazek %s-bb-media obnoven\n' "$project"
        fi
        # Jen databáze a úložiště: aplikace se spustí až nad obnovenými daty.
        compose up --detach --build --wait --wait-timeout 120 jrc-postgres jrc-s3 jrc-dev
        sh scripts/workspace.sh check
        compose run --rm --no-deps -T --volume "$abs:/backup:ro" jrc-dev \
            python3 "$tool" restore-data --project "$project" --backup /backup
        ;;
    restore-finish)
        restore_guard
        sh scripts/workspace.sh check
        compose exec -T jrc-bb-web python manage.py jrc_sync_freeframe
        printf 'Obnova do projektu %s je hotová.\n' "$project"
        ;;
    *) die "Neznámá operace: $1" ;;
esac
