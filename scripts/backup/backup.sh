#!/bin/sh
# Zálohy a obnova JR Content (F2-B09): řízení Dockeru na hostiteli. Vlastní práce běží v Dockeru
# (scripts/backup/jrc_backup.py). Volá se z Makefile: make backup | backup-verify | restore.
# Záloha jen čte. Obnova nikdy nesmí zasáhnout projekt `jrc` ani svazky, ve kterých už něco je.
set -eu

project=${JRC_PROJECT:-jrc}
workdir=$(pwd -P)
dev_image="jrc-dev:$project"
tool=scripts/backup/jrc_backup.py
# Integrační test předá značku: při selhání Dockeru nesmí následovat destruktivní úklid.
docker() {
    if command docker "$@"; then return 0; else
        status=$?
        if [ -n "${JRC_BACKUP_DOCKER_FAILURE_FILE:-}" ]; then
            : > "$JRC_BACKUP_DOCKER_FAILURE_FILE"
        fi
        return "$status"
    fi
}

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
    JRC_PROJECT="$project" docker compose --project-name "$project" --file docker-compose.yml --env-file .env "$@"
}

need_image() {
    docker image inspect "$dev_image" >/dev/null 2>&1 || die "Chybí obraz $dev_image (spusť make build v tomto projektu)."
}

# Absolutní cesta k záloze; obsah se nikdy nemění (připojuje se jen pro čtení).
safe_backup_path() {
    candidate=$1
    case "$candidate" in /*) ;; *) candidate="$workdir/$candidate" ;; esac
    case "$candidate" in "$workdir/backups/"*|"$workdir/backups") ;; *) die 'Cesta musí ležet v backups/ této pracovní kopie.' ;; esac
    rest=${candidate#/}
    prefix=
    while [ -n "$rest" ]; do
        part=${rest%%/*}
        case "$part" in ''|.|..) die 'Neplatná komponenta cesty zálohy.' ;; esac
        prefix="$prefix/$part"
        [ ! -L "$prefix" ] || die "Symbolický odkaz v cestě zálohy: $prefix"
        case "$rest" in */*) rest=${rest#*/} ;; *) rest= ;; esac
    done
    printf '%s\n' "$candidate"
}
backup_path() {
    [ -n "${1:-}" ] || die 'Chybí BACKUP=<cesta k záloze>'
    abs=$(safe_backup_path "$1")
    [ -d "$abs" ] || die "Záloha $1 neexistuje."
    [ -f "$abs/manifest.json" ] || die "V $1 chybí manifest.json; není to hotová záloha."
    printf '%s\n' "$abs"
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
    volumes=$(docker volume ls --format '{{.Name}}') || { printf 'CHYBA: Nelze zjistit svazky Dockeru.\n' >&2; return 2; }
    printf '%s\n' "$volumes" | grep -Fxq "$1"
}

# Svazek smí neexistovat, nebo v něm smí být nanejvýš .jrc-working-dir.
require_empty_volume() {
    volume=$1
    if volume_exists "$volume"; then :; else
        status=$?
        [ "$status" = 1 ] || die "Chyba Dockeru při kontrole svazku."
        return 0
    fi
    content=$(docker run --rm --pull never --network none --user 0:0 --cap-drop ALL --cap-add DAC_READ_SEARCH \
        --mount "type=volume,src=$volume,dst=/v,readonly" "$dev_image" \
        sh -c 'if [ -L /v/.jrc-working-dir ] || { [ -e /v/.jrc-working-dir ] && [ ! -f /v/.jrc-working-dir ]; }; then printf invalid-marker; else find /v -mindepth 1 -maxdepth 1 -not -name .jrc-working-dir -print -quit; fi') || die "Nelze zkontrolovat svazek $volume."
    if [ -n "$content" ]; then
        printf 'CHYBA: Svazek %s už obsahuje data. Obnova se provádí jen do PRÁZDNÉHO projektu.\n' "$volume" >&2
        printf 'Chceš-li ověřovací projekt %s začít znovu (smaže JEN jeho data):\n' "$project" >&2
        printf '  JRC_PROJECT=%s docker compose --project-name %s --file docker-compose.yml --env-file .env down --volumes\n' "$project" "$project" >&2
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
        safe_backup_path "$workdir/backups/$project" >/dev/null
        sh scripts/workspace.sh check
        need_image
        rev=$(git rev-parse HEAD 2>/dev/null || printf unknown)
        dirty=no
        if [ -n "$(git status --porcelain 2>/dev/null | head -n 1)" ]; then dirty=yes; fi
        # Běžící prostředí se jen čte; Garage musí obsahovat jen buckety, které umíme zálohovat.
        bucket_output=$(compose exec -T jrc-s3 garage bucket list) || die 'Nelze zjistit seznam bucketů Garage.'
        # Garage 2.4: první řádek je hlavička; třetí sloupec obsahuje lokální alias.
        buckets=$(printf '%s\n' "$bucket_output" | awk 'NR == 1 {if ($0 !~ /^ID[[:space:]]+Created[[:space:]]+Global aliases[[:space:]]+Local aliases[[:space:]]*$/) exit 1; next} NF {if (NF != 3 || $3 !~ /^jrc-[a-z0-9-]+$/) exit 1; print $3}') \
            || die 'Neplatný seznam bucketů Garage.'
        [ -n "$buckets" ] || die 'Prázdný seznam bucketů Garage.'
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
        # Uchováme objekty už před dumpy; druhý průchod doplní nové, přepsané klíče odmítne.
        compose exec -T jrc-dev python3 "$tool" copy-objects --project "$project" --stamp "$stamp"
        # pg_dump musí mít stejnou hlavní verzi jako server, proto běží v kontejneru PostgreSQL (jen čtení).
        printf 'Záloha %s/%s: databáze\n' "$project" "$stamp"
        for database in brightbean freeframe; do
            compose exec -T jrc-postgres sh -c 'pg_dump --format=custom --no-password --username "$POSTGRES_USER" "$1"' sh "$database" \
                > "$partial/postgres/$database.dump"
            compose exec -T jrc-postgres pg_restore --list < "$partial/postgres/$database.dump" > "$partial/postgres/$database.list"
            grep -q 'TABLE DATA' "$partial/postgres/$database.list" || die "Výpis databáze $database je prázdný nebo poškozený."
            rm "$partial/postgres/$database.list"
            compose exec -T jrc-postgres pg_restore --data-only --file=- < "$partial/postgres/$database.dump" > "$partial/postgres/$database.data.sql"
            compose exec -T jrc-dev python3 "$tool" references --project "$project" --stamp "$stamp" --database "$database"
            printf '  %s: %s bajtů\n' "$database" "$(wc -c < "$partial/postgres/$database.dump")"
        done
        # Soubory v Garage se kopírují až po databázi (viz omezení v návodu).
        compose exec -T jrc-dev python3 "$tool" copy-objects --project "$project" --stamp "$stamp"
        media=absent
        if volume_exists "$project-bb-media"; then
            docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
                --mount "type=volume,src=$project-bb-media,dst=/media,readonly" \
                --mount "type=bind,src=$workdir/$partial,dst=/out" \
                "$dev_image" tar -cf /out/bb-media.tar -C /media .
            media=present
            printf '  svazek %s-bb-media: zálohován\n' "$project"
        else
            status=$?
            [ "$status" = 1 ] || die "Chyba Dockeru při hledání médií."
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
    restore-volume-check)
        # Samostatná kontrola bez zápisu, také pro test prázdného svazku bez startu služeb.
        restore_guard
        need_image
        for name in postgres-data s3-data bb-media redis-data; do
            require_empty_volume "$project-$name"
        done
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
            if volume_exists "$project-bb-media"; then :; else
                status=$?
                [ "$status" = 1 ] || die "Chyba Dockeru při hledání médií."
                docker volume create --label "com.docker.compose.project=$project" --label com.docker.compose.volume=jrc-bb-media "$project-bb-media" >/dev/null
            fi
            docker run --rm --pull never --network none --user 0:0 \
                --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
                --mount "type=volume,src=$project-bb-media,dst=/media" \
                --mount "type=bind,src=$abs,dst=/backup,readonly" \
                "$dev_image" sh -c \
                'content=$(find /media -mindepth 1 -maxdepth 1 -print -quit) || exit 4; test -z "$content" || exit 3; tar -xf /backup/bb-media.tar -C /media --no-same-owner && chown -R "$1:$2" /media' \
                sh "$(id -u)" "$(id -g)"
            printf 'Svazek %s-bb-media obnoven\n' "$project"
        fi
        # Jen databáze a úložiště: aplikace se spustí až nad obnovenými daty.
        compose up --detach --build --wait --wait-timeout 120 jrc-postgres jrc-s3 jrc-dev
        sh scripts/workspace.sh check
        compose run --rm --no-deps -T --volume "$abs:/backup:ro" jrc-dev \
            python3 "$tool" restore-check --project "$project" --backup /backup
        # pg_restore stejné hlavní verze jako server (v kontejneru PostgreSQL); jedna transakce, při chybě se nic nevloží.
        for database in brightbean freeframe; do
            printf 'Obnova databáze %s\n' "$database"
            compose exec -T jrc-postgres sh -c \
                'pg_restore --single-transaction --exit-on-error --no-owner --no-privileges --no-password --username "$POSTGRES_USER" --dbname "$1"' \
                sh "$database" < "$abs/postgres/$database.dump"
        done
        compose run --rm --no-deps -T --volume "$abs:/backup:ro" jrc-dev \
            python3 "$tool" restore-objects --project "$project" --backup /backup
        ;;
    restore-finish)
        restore_guard
        sh scripts/workspace.sh check
        compose exec -T jrc-bb-web python manage.py jrc_sync_freeframe
        printf 'Obnova do projektu %s je hotová.\n' "$project"
        ;;
    *) die "Neznámá operace: $1" ;;
esac
