#!/bin/sh
set -eu

project=${JRC_PROJECT:-jrc}
[ "$project" = jrc ] && exit 0

config_value() {
    name=$1
    fallback=$2
    eval "value=\${$name-}"
    if [ -n "$value" ]; then
        printf '%s' "$value"
    elif [ -f .env ]; then
        value=$(sed -n "s/^${name}=//p" .env | head -n 1)
        printf '%s' "${value:-$fallback}"
    else
        printf '%s' "$fallback"
    fi
}

app_port=$(config_value JRC_APP_PORT 13000)
s3_port=$(config_value JRC_S3_PORT 13900)
if [ "$app_port" = 13000 ] || [ "$s3_port" = 13900 ]; then
    printf 'Projekt %s nesmí použít port hlavního projektu jrc (APP 13000, S3 13900). Aktuálně: APP %s, S3 %s. Nastavte vlastní JRC_APP_PORT a JRC_S3_PORT v .env.\n' \
        "$project" "$app_port" "$s3_port" >&2
    exit 1
fi
