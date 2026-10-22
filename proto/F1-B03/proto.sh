#!/usr/bin/env bash
# Ovládání prototypu F1-B03 (verzované texty). Spouští se na Jarvisovi, vše běží v Dockeru:
#   jrc-proto-f1-b03     vývojový kontejner (obraz jrc-dev:local), aplikace na 127.0.0.1:13031
#   jrc-proto-f1-b03-db  PostgreSQL 18.6, data ve svazku jrc-proto-f1-b03-db-data, 127.0.0.1:15531
# Příkazy: up | test | restart | status | logs | down | destroy
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(cd "$DIR/../.." && pwd)"
P=jrc-proto-f1-b03
NET="$P-net"
DB="$P-db"
VOL="$P-db-data"
APP="$P"
APP_PORT=13031
DB_PORT=15531
WORKDIR=/workspace/proto/F1-B03

ensure_env() {
  if [ ! -f "$DIR/.env" ]; then
    umask 077
    printf 'PROTO_DB_PASSWORD=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')" > "$DIR/.env"
  fi
  # shellcheck disable=SC1091
  . "$DIR/.env"
}

exists() { docker container inspect "$1" >/dev/null 2>&1; }
running() { [ "$(docker container inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }

wait_db() {
  for _ in $(seq 1 60); do
    if docker exec "$DB" pg_isready -U proto -d proto >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "Databáze nenaběhla." >&2; return 1
}

start_server() {
  docker exec "$APP" sh -c 'if [ -f /tmp/proto-server.pid ]; then kill "$(cat /tmp/proto-server.pid)" 2>/dev/null || true; sleep 1; fi'
  docker exec -d -w "$WORKDIR" "$APP" sh -c 'echo $$ > /tmp/proto-server.pid; exec node server/index.js >> /tmp/proto-server.log 2>&1'
  for _ in $(seq 1 30); do
    if docker exec "$APP" node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"; then
      echo "Aplikace běží: http://127.0.0.1:$APP_PORT"; return 0
    fi
    sleep 1
  done
  echo "Aplikace nenaběhla, viz: $0 logs" >&2; return 1
}

up() {
  ensure_env
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
  if ! exists "$DB"; then
    docker run -d --name "$DB" --network "$NET" \
      -e POSTGRES_USER=proto -e POSTGRES_DB=proto -e POSTGRES_PASSWORD="$PROTO_DB_PASSWORD" \
      -v "$VOL:/var/lib/postgresql" -p "127.0.0.1:$DB_PORT:5432" postgres:18.6-bookworm >/dev/null
  elif ! running "$DB"; then
    docker start "$DB" >/dev/null
  fi
  if ! exists "$APP"; then
    docker run -d --name "$APP" --user 1000:1000 -e HOME=/tmp/h \
      -e PGHOST="$DB" -e PGPORT=5432 -e PGUSER=proto -e PGDATABASE=proto -e PGPASSWORD="$PROTO_DB_PASSWORD" \
      --network "$NET" -v "$WT:/workspace" -w /workspace -p "127.0.0.1:$APP_PORT:3000" \
      jrc-dev:local sleep infinity >/dev/null
  elif ! running "$APP"; then
    docker start "$APP" >/dev/null
  fi
  wait_db
  docker exec -w "$WORKDIR" "$APP" sh -c 'npm ci --no-audit --no-fund >/dev/null && npm run build'
  start_server
}

case "${1:-help}" in
  up) up ;;
  test)
    # Testy používají vlastní databázi proto_test a vlastní server na portu 3100.
    docker exec -w "$WORKDIR" "$APP" npx playwright test "${@:2}" ;;
  restart)
    # Restart databáze i aplikace: ověření, že verze zůstanou uložené.
    docker restart "$DB" >/dev/null; wait_db; start_server ;;
  status) docker ps -a --filter "name=$P" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' ;;
  logs) docker exec "$APP" tail -n 100 /tmp/proto-server.log ;;
  down)
    # Zastaví a smaže kontejnery, data databáze ve svazku zůstanou.
    docker rm -f "$APP" "$DB" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true ;;
  destroy)
    # Jako down a navíc smaže data databáze.
    docker rm -f "$APP" "$DB" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    docker volume rm "$VOL" >/dev/null 2>&1 || true ;;
  *) echo "Použití: $0 up|test|restart|status|logs|down|destroy" ;;
esac
