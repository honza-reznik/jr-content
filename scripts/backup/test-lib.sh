#!/bin/sh
# Společné vlastnictví testovacích prostředků. Žádná chyba Dockeru se nesmí změnit na prázdný seznam.
fail() { printf 'CHYBA TESTU: %s\n' "$*" >&2; exit 1; }
checked_docker() {
    if docker "$@"; then return 0; else
        status=$?
        : > "$run_dir/docker-failed"
        printf 'CHYBA Dockeru, okamžitý konec bez dalšího úklidu.\n' >&2
        exit "$status"
    fi
}
require_local_path() {
    local_root=$(pwd -P)
    case "$1" in /*|*../*|..|*/..) fail 'Neplatná místní cesta.' ;; esac
    remainder=$1
    current=$local_root
    while [ -n "$remainder" ]; do
        component=${remainder%%/*}
        case "$component" in ''|.|..) fail 'Neplatná komponenta místní cesty.' ;; esac
        current="$current/$component"
        [ ! -L "$current" ] || fail "Symbolický odkaz v místní cestě: $current"
        case "$remainder" in */*) remainder=${remainder#*/} ;; *) remainder= ;; esac
    done
}
new_run() {
    require_local_path .cache
    require_local_path backups
    mkdir -p .cache || fail "Nelze vytvořit pracovní adresář testu."
    run_dir=$(mktemp -d "$PWD/.cache/f2b09-XXXXXXXXXXXX") || fail "Nelze rezervovat jedinečný test."
    run_id=$(basename "$run_dir" | tr 'A-Z' 'a-z')
}
require_unused_project() {
    project_to_check=$1
    case "$project_to_check" in jrc-*) ;; *) fail 'Test potřebuje oddělený projekt.' ;; esac
    # Zjišťujeme i zastavené kontejnery a nepoužité obrazy/svazky. Selhání = konec, bez trapu s mazáním.
    for kind in volume network container image; do
        case "$kind" in
            volume) names=$(checked_docker volume ls --format '{{.Name}}') || exit $? ;;
            network) names=$(checked_docker network ls --format '{{.Name}}') || exit $? ;;
            container) names=$(checked_docker ps -a --format '{{.Names}}') || exit $? ;;
            image) names=$(checked_docker image ls --format '{{.Repository}}:{{.Tag}}') || exit $? ;;
        esac
        for resource in $names; do
            case "$resource" in "$project_to_check"|"$project_to_check-"*|"${project_to_check}_"*|*:"$project_to_check")
                fail "Projekt $project_to_check už má prostředek $resource; nic se nemění." ;;
            esac
        done
    done
    [ ! -e "backups/$project_to_check" ] && [ ! -L "backups/$project_to_check" ] || fail 'Adresář záloh projektu už existuje.'
}
# Před down --volumes zkontrolujeme každý svazek s naším prefixem i každý se štítkem projektu.
# Nestačí pouze odhad podle jména; Compose musí doložit stejné vlastnictví.
check_owned_project() {
    owned=$1
    all=$(checked_docker volume ls --format '{{.Name}}') || exit $?
    for resource in $all; do
        case "$resource" in "$owned-"*)
            label=$(checked_docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$resource") || exit $?
            [ "$label" = "$owned" ] || fail "Nedoložené vlastnictví svazku $resource; úklid zastaven." ;;
        esac
    done
    for kind in container network; do
        case "$kind" in
            container) resources=$(checked_docker ps -aq --filter "label=com.docker.compose.project=$owned") || exit $? ;;
            network) resources=$(checked_docker network ls -q --filter "label=com.docker.compose.project=$owned") || exit $? ;;
        esac
        for resource in $resources; do
            if [ "$kind" = container ]; then
                label=$(checked_docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$resource") || exit $?
            else
                label=$(checked_docker network inspect --format '{{index .Labels "com.docker.compose.project"}}' "$resource") || exit $?
            fi
            [ "$label" = "$owned" ] || fail 'Cizí prostředek při úklidu.'
        done
    done
}
cleanup_project() {
    owned=$1
    check_owned_project "$owned"
    JRC_PROJECT="$owned" checked_docker compose --project-name "$owned" --file docker-compose.yml --env-file .env down --volumes --remove-orphans
    # Tagy jsme rezervovali před sestavením. Pokud se mezitím změnil obraz, cizí tag nesmažeme.
    images=$(checked_docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}') || exit $?
    printf '%s\n' "$images" > "$run_dir/current-images"
    if [ -f "$run_dir/$owned.images" ]; then
        while read -r tag image_id; do
            case "$tag" in *:"$owned") ;;
                *) fail 'Neplatný záznam vlastnictví obrazu.' ;;
            esac
            current=$(awk -v t="$tag" '$1==t {print $2}' "$run_dir/current-images")
            [ "$current" = "$image_id" ] || fail "Obraz $tag se změnil; úklid zastaven."
            checked_docker image rm "$tag"
        done < "$run_dir/$owned.images"
    fi
}
record_images() {
    owned=$1
    images=$(checked_docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}') || exit $?
    printf '%s\n' "$images" | awk -v suffix=":$owned" 'substr($1,length($1)-length(suffix)+1)==suffix' > "$run_dir/$owned.images"
}
# Přímé HTTP zápisy mohou začít až po shodě projektu, portu a místního bindu proxy.
require_proxy_origin() {
    proxy_project=$1
    proxy_expected_port=$2
    proxy_id=$(JRC_PROJECT="$proxy_project" checked_docker compose --project-name "$proxy_project" --file docker-compose.yml --env-file .env ps -q jrc-proxy) || exit $?
    [ -n "$proxy_id" ] || fail 'Chybí proxy.'
    actual_project=$(checked_docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$proxy_id") || exit $?
    actual_port=$(checked_docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "$proxy_id") || exit $?
    actual_ip=$(checked_docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostIp}}' "$proxy_id") || exit $?
    [ "$actual_project" = "$proxy_project" ] && [ "$actual_port" = "$proxy_expected_port" ] && [ "$actual_ip" = 127.0.0.1 ] \
        || fail 'Port nepatří proxy testovacího projektu.'
}
new_foreign_fixture() {
    foreign=$(mktemp -d "backups/$1/nechat-byt-XXXXXXXXXXXX") || fail "Nelze rezervovat testovací složku."
    printf 'cizí složka bez manifestu\n' > "$foreign/poznamka.txt"
    printf '%s\n' "$foreign"
}
# Prostředí příkazu má přednost před .env, stejně jako při spuštění Compose.
env_port() {
    case "$1" in JRC_APP_PORT|JRC_S3_PORT|JRC_POSTGRES_PORT|JRC_BB_PORT) ;; *) fail 'Neznámé nastavení portu.' ;; esac
    value=$(eval "printf '%s' \"\${$1:-}\"")
    [ -n "$value" ] || value=$(sed -n "s/^$1=//p" .env | head -n 1)
    printf '%s' "$value"
}
seed_freeframe() {
    seed_project=$1
    seed_app=$(env_port JRC_APP_PORT)
    seed_s3=$(env_port JRC_S3_PORT)
    require_proxy_origin "$seed_project" "$seed_app"
    checked_docker run --rm --network host --pull never --user "$(id -u):$(id -g)" --env-file .env \
        --volume "$PWD:/jrc" --workdir /jrc --env JRC_APP_PORT="$seed_app" --env JRC_S3_PORT="$seed_s3" \
        --env JRC_PUBLIC_ORIGIN --env JRC_BASE_PATH --env JRC_TEST_PROJECT="$seed_project" --env JRC_TEST_ORIGIN_VERIFIED=1 \
        "jrc-ff-api:$seed_project" python scripts/backup/seed-ff.py
}
