#!/bin/sh
# Hostitel pouze řídí kontejnery; databáze i .env upravuje nástroj v Dockeru.
set -eu
project=${JRC_PROJECT:-jrc}
printf '%s' "$project" | grep -Eqx 'jrc-[a-z0-9-]+' || {
    printf 'Anonymizace projektu jrc je zakázaná; použij JRC_PROJECT=jrc-<název>.\n' >&2
    exit 1
}
compose() {
    JRC_PROJECT="$project" docker compose --project-name "$project" --file docker-compose.yml --env-file .env "$@"
}
sh scripts/workspace.sh check
# Quiesce writers/senders before either DB changes. On error they stay stopped.
running=$(compose ps --status running --services)
apps=
for service in $running; do
    case "$service" in jrc-postgres|jrc-s3|jrc-dev) ;; *) apps="$apps $service" ;; esac
done
if [ -n "$apps" ]; then
    # Names are supplied by Compose, not arbitrary user input.
    # shellcheck disable=SC2086
    compose stop $apps >/dev/null 2>&1 || {
        printf 'Zastavení služeb selhalo; anonymizace se neprovede.\n' >&2
        exit 1
    }
fi
compose exec -T -e JRC_FF_SYNC_EMAIL jrc-dev timeout 300s python3 scripts/backup/sanitize_test.py --project "$project"
# The updated .env is authoritative even if the old login came from the shell.
unset JRC_FF_SYNC_EMAIL
if [ -n "$apps" ]; then
    # Recreate to read the sanitized service login from .env, preserving its UUID.
    # shellcheck disable=SC2086
    compose up --detach --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 180 $apps >/dev/null 2>&1 || {
        printf 'Anonymizace hotová, ale spuštění služeb selhalo; zkontrolujte jejich stav.\n' >&2
        exit 1
    }
fi
