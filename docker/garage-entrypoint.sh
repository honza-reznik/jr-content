#!/bin/sh
set -eu
: "${GARAGE_RPC_SECRET:?Chybí GARAGE_RPC_SECRET v .env}"
: "${AWS_ACCESS_KEY_ID:?Chybí AWS_ACCESS_KEY_ID v .env}"
: "${AWS_SECRET_ACCESS_KEY:?Chybí AWS_SECRET_ACCESS_KEY v .env}"
: "${S3_BUCKET:?Chybí S3_BUCKET v .env}"
mkdir -p /var/lib/garage/meta /var/lib/garage/data
garage server &
server_pid=$!
trap 'kill -TERM "$server_pid" 2>/dev/null || true; wait "$server_pid" || true' TERM INT
# Nastavení běží jen při prvním startu nového datového svazku.
if [ ! -f /var/lib/garage/meta/.jrc-initialized ]; then
    attempts=0
    until garage status >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 60 ] || ! kill -0 "$server_pid" 2>/dev/null; then
            echo 'Garage se nepodařilo spustit.' >&2
            kill "$server_pid" 2>/dev/null || true
            exit 1
        fi
        sleep 1
    done
    node_id=$(garage node id 2>/dev/null | cut -d@ -f1)
    garage layout assign -z local -c 1G "$node_id"
    garage layout apply --version 1
    # Výstup importu obsahuje přístupový klíč; nesmí do logu.
    garage key import --yes -n jrc-local "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" >/dev/null 2>&1
    garage bucket create "$S3_BUCKET"
    garage bucket allow --read --write --owner "$S3_BUCKET" --key "$AWS_ACCESS_KEY_ID" >/dev/null 2>&1
    touch /var/lib/garage/meta/.jrc-initialized
fi
# Samostatný video klíč doplníme i na svazku vytvořeném před F2-B02.
if [ -n "${JRC_FF_S3_ACCESS_KEY:-}" ] && [ ! -f /var/lib/garage/meta/.jrc-video-initialized ]; then
    : "${JRC_FF_S3_SECRET_KEY:?Chybí JRC_FF_S3_SECRET_KEY}"
    attempts=0
    until garage status >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 60 ] || ! kill -0 "$server_pid" 2>/dev/null; then
            echo 'Garage není připravené pro inicializaci videí.' >&2
            kill "$server_pid" 2>/dev/null || true
            exit 1
        fi
        sleep 1
    done
    garage key import --yes -n jrc-video "$JRC_FF_S3_ACCESS_KEY" "$JRC_FF_S3_SECRET_KEY" >/dev/null 2>&1
    garage bucket info jrc-video >/dev/null 2>&1 || garage bucket create jrc-video
    garage bucket allow --read --write --owner jrc-video --key "$JRC_FF_S3_ACCESS_KEY" >/dev/null 2>&1
    touch /var/lib/garage/meta/.jrc-video-initialized
fi
wait_ready() {
    attempts=0
    until garage status >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 60 ] || ! kill -0 "$server_pid" 2>/dev/null; then
            echo "Garage není připravené ($1)." >&2
            kill "$server_pid" 2>/dev/null || true
            exit 1
        fi
        sleep 1
    done
}
# F2-B05: soukromý bucket Brightbeanu s vlastním klíčem, také na svazku z dřívějška.
if [ ! -f /var/lib/garage/meta/.jrc-app-initialized ]; then
    : "${JRC_BB_S3_ACCESS_KEY:?Chybí JRC_BB_S3_ACCESS_KEY v .env (make env)}"
    : "${JRC_BB_S3_SECRET_KEY:?Chybí JRC_BB_S3_SECRET_KEY v .env (make env)}"
    wait_ready 'bucket jrc-app'
    # Výpisy klíčů obsahují tajnou část; nic z nich nesmí do logu.
    garage key info "$JRC_BB_S3_ACCESS_KEY" >/dev/null 2>&1 || \
        garage key import --yes -n jrc-app "$JRC_BB_S3_ACCESS_KEY" "$JRC_BB_S3_SECRET_KEY" >/dev/null 2>&1
    garage bucket info jrc-app >/dev/null 2>&1 || garage bucket create jrc-app >/dev/null
    # Vlastník smí nastavit CORS bucketu (nastavuje Brightbean při migraci).
    garage bucket allow --read --write --owner jrc-app --key "$JRC_BB_S3_ACCESS_KEY" >/dev/null 2>&1
    touch /var/lib/garage/meta/.jrc-app-initialized
fi
# Kvóty jsou tvrdý strop místa na disku Jarvise (AK6). Nastavují se při každém startu
# podle .env; značka v /tmp zaniká s kontejnerem, takže zdraví čeká na jejich použití.
wait_ready 'kvóty'
garage bucket set-quotas --max-size "${JRC_S3_APP_QUOTA:-20GiB}" jrc-app >/dev/null
if garage bucket info jrc-video >/dev/null 2>&1; then
    garage bucket set-quotas --max-size "${JRC_S3_VIDEO_QUOTA:-100GiB}" jrc-video >/dev/null
fi
touch /tmp/.jrc-ready
wait "$server_pid"
