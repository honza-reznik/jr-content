#!/bin/sh
# Ovládání Dockeru na hostiteli; nástroje a práce s datovými svazky běží v Dockeru.
set -eu
current_dir=$(pwd -P)

dev_dir() {
    docker inspect jrc-dev --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null || true
}

existing_volumes() {
    # Chyba spojení s Dockerem nesmí být vyhodnocena jako prázdné úložiště.
    all_volumes=$(docker volume ls --format '{{.Name}}') || return 1
    for volume in $all_volumes; do
        case "$volume" in
            jrc-postgres-data|jrc-s3-data) printf '%s\n' "$volume" ;;
        esac
    done
}

recovery_error() {
    printf 'Chybí .env, ale existují datové svazky JR Content. Nová hesla nelze bezpečně vytvořit.\n' >&2
    printf 'Zkopírujte původní .env do aktuální složky (chmod 600 .env), nebo spusťte make reset, který SMAŽE lokální data JR Content.\n' >&2
    exit 1
}

case "${1:?Chybí operace}" in
    check)
        owner=$(dev_dir)
        running=$(docker inspect jrc-dev --format '{{.State.Running}}' 2>/dev/null || true)
        if [ "$owner" != "$current_dir" ] || [ "$running" != true ]; then
            printf 'Vývojové prostředí neběží pro aktuální složku %s.\n' "$current_dir" >&2
            printf 'Složka posledního jrc-dev: %s\n' "${owner:-nezjištěna}" >&2
            printf 'Stačí spustit make up v aktuální složce; prostředí se na ni přepne.\n' >&2
            exit 1
        fi
        if [ ! -f .env ]; then
            printf 'Chybí .env. Spusťte make up v aktuální složce pro jeho obnovení.\n' >&2
            exit 1
        fi
        ;;
    env)
        [ ! -f .env ] || exit 0
        volumes=$(existing_volumes)
        [ -n "$volumes" ] || exit 0
        owner=$(dev_dir)
        # Po make down již kontejner neexistuje. Cesta (bez hesel) zůstává ve svazcích.
        if [ -z "$owner" ]; then
            for volume in $volumes; do
                owner=$(docker run --rm --name jrc-state --pull never --network none \
                    --mount "type=volume,src=$volume,dst=/state,readonly" \
                    jrc-dev:local sh -c 'if [ -f /state/.jrc-working-dir ]; then cat /state/.jrc-working-dir; fi') || owner=
                [ -z "$owner" ] || break
            done
        fi
        if [ -n "$owner" ] && [ "$owner" != "$current_dir" ] && [ -r "$owner/.env" ]; then
            # Neúspěšná kopie nesmí zanechat neúplný .env pro další make up.
            env_copy=$(mktemp .jrc-env.XXXXXX.env.local) || recovery_error
            trap 'rm -f "$env_copy"' 0
            if ! (cp "$owner/.env" "$env_copy" && chmod 600 "$env_copy" && mv "$env_copy" .env); then
                recovery_error
            fi
            printf 'Převzat původní .env ze složky %s; hesla i data zůstávají zachovaná.\n' "$owner"
        else
            recovery_error
        fi
        ;;
    remember)
        owner=$(dev_dir)
        [ -n "$owner" ] || exit 0
        volumes=$(existing_volumes)
        for volume in $volumes; do
            docker run --rm --name jrc-state --pull never --network none \
                --mount "type=volume,src=$volume,dst=/state" \
                jrc-dev:local sh -c 'printf "%s\n" "$1" > /state/.jrc-working-dir' sh "$owner"
        done
        ;;
    *) printf 'Neznámá operace: %s\n' "$1" >&2; exit 1 ;;
esac
