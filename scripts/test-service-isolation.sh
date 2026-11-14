#!/bin/sh
# Hostitel jen řídí Docker/inspect; síťové zkoušky běží v kontejnerech.
set -eu
project=${JRC_PROJECT:?Chybí JRC_PROJECT}
# Nevynecháme chybějící službu ani dodatečný kontejner stejného projektu.
for service in $("$@" config --services); do
  test -n "$("$@" ps --all --quiet "$service")" || {
    echo "Chybí služba $service"; exit 1;
  }
done
containers=$(docker ps --all --quiet --filter "label=com.docker.compose.project=$project")
test -n "$containers"
for container in $containers; do
  service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container")
  networks=$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$container")
  test -n "$networks"
  for network in $networks; do
    if [ "$(docker network inspect --format '{{.Internal}}' "$network")" != true ]; then
      # Docker 29 pro publikaci portů vyžaduje vstupní síť. Jen obě brány,
      # bez NAT; jejich nepřítomné trasy a skutečné spojení ověří probe níže.
      case "$service:$network" in
        "jrc-proxy:${project}_ingress"|"jrc-ff-gate:${project}_ingress")
          test "$(docker network inspect --format '{{index .Options "com.docker.network.bridge.enable_ip_masquerade"}}' "$network")" = false ;;
        "jrc-smtp-relay:${project}_smtp-egress")
          # The dedicated relay is the only service allowed onto the SMTP egress network.
          test "$(docker network inspect --format '{{.Internal}}' "$network")" = false ;;
        "jrc-favicon-relay:${project}_favicon-egress")
          # This relay accepts only bounded public favicon requests, with no app credentials.
          test "$(docker network inspect --format '{{.Internal}}' "$network")" = false ;;
        *) echo "CHYBA: $service má síť s internetem: $network"; exit 1 ;;
      esac
    fi
  done
  case "$service" in
    jrc-ff-gate|jrc-proxy)
      test "$(docker inspect --format '{{json .HostConfig.Dns}}' "$container")" = '["127.0.0.1"]'
      bindings=$(docker inspect --format '{{range .HostConfig.PortBindings}}{{range .}}{{.HostIp}}{{"\n"}}{{end}}{{end}}' "$container")
      test -n "$bindings"
      test -z "$(printf '%s\n' "$bindings" | sed '/^127\.0\.0\.1$/d')"
      ports=$(docker inspect --format '{{range $port, $_ := .HostConfig.PortBindings}}{{$port}} {{end}}' "$container")
      case "$service:$ports" in
        'jrc-ff-gate:13900/tcp '|'jrc-proxy:8080/tcp ') ;;
        *) echo "CHYBA: $service publikuje neočekávané porty"; exit 1 ;;
      esac
      # PID 1 je přímo běžná aplikace, startovací oprávnění nezůstala ani v initu.
      "$@" exec -T "$service" timeout 30s node --max-old-space-size=2048 -e '
        const assert = require("node:assert/strict");
        const status = require("node:fs").readFileSync("/proc/1/status", "utf8");
        assert(!/^Uid:\s+0\s/m.test(status), "Brána běží jako root");
        for (const field of ["CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"]) {
          assert(new RegExp(`^${field}:\\s+0+$`, "m").test(status), `Bráně zůstalo ${field}`);
        }
      '
      ;;
    *)
      test "$(docker inspect --format '{{len .HostConfig.PortBindings}}' "$container")" = 0 || {
        echo "CHYBA: $service publikuje vlastní port"; exit 1;
      }
      ;;
  esac
  case "$service" in
    jrc-smtp-relay|jrc-smtp-test) ;;
    *)
      for setting in NEXT_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 NPM_CONFIG_AUDIT=false \
        NPM_CONFIG_FUND=false NPM_CONFIG_UPDATE_NOTIFIER=false PIP_DISABLE_PIP_VERSION_CHECK=1; do
        docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" | grep -Fxq "$setting" || {
          echo "CHYBA: $service nemá $setting"; exit 1;
        }
      done
      ;;
  esac
  if [ "$service" = jrc-smtp-relay ]; then
    environment=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container")
    printf '%s\n' "$environment" | grep -Eq '^SMTP_TARGET_HOST=.+$'
    printf '%s\n' "$environment" | grep -Eq '^SMTP_TARGET_PORT=[0-9]+$'
    printf '%s\n' "$environment" | grep -Eq '^SMTP_LISTEN_PORT=[0-9]+$'
    echo "OK $service: jediná služba s řízeným SMTP výstupem"
  elif [ "$service" = jrc-favicon-relay ]; then
    test "$(docker inspect --format '{{.Config.User}}' "$container")" = '65534:65534'
    test "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$container")" = true
    test "$(docker inspect --format '{{.HostConfig.Memory}}' "$container")" = 134217728
    test "$(docker inspect --format '{{.HostConfig.PidsLimit}}' "$container")" = 16
    test "$(docker inspect --format '{{json .HostConfig.CapDrop}}' "$container")" = '["ALL"]'
    test "$(docker inspect --format '{{json .HostConfig.CapAdd}}' "$container")" = null
    test "$(docker inspect --format '{{json .Config.Entrypoint}}' "$container")" = '["python","/relay/channel-favicon-relay.py"]'
    test "$(docker inspect --format '{{len .Mounts}}' "$container")" = 2
    for mount in '/relay/channel-favicon-relay.py false' '/favicon/favicon_fetch.py false'; do
      docker inspect --format '{{range .Mounts}}{{.Destination}} {{.RW}}{{"\n"}}{{end}}' "$container" | grep -Fxq "$mount"
    done
    if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" | grep -Eq '^(DB_|PG|AWS_|JRC_|SMTP_|SECRET_|ENCRYPTION_)'; then
      echo "CHYBA: favicon relay má přístupové nastavení aplikace"; exit 1
    fi
    echo "OK $service: omezený výstup pro favicony, bez tajemství, pouze dva soubory, bez portů hostitele"
  elif [ "$(docker inspect --format '{{.State.Running}}' "$container")" = true ]; then
    # Sdílený síťový prostor ověří i služby bez Node.js (Garage, Redis, Caddy).
    docker run --rm --pull never --memory=512m --memory-swap=512m --cpus=1 --pids-limit=64 --network "container:$container" \
      --user "$(id -u):$(id -g)" --volume "$PWD/scripts/probe-isolation.cjs:/probe.cjs:ro" \
      "jrc-dev:$project" timeout 30s node --max-old-space-size=2048 /probe.cjs
    echo "OK $service: bez výchozí trasy, porty jen místně, telemetrie vypnutá, IPv4 i IPv6 ven zablokované"
  else
    test "$(docker inspect --format '{{.State.ExitCode}}' "$container")" = 0
    echo "OK $service: dokončená inicializace, jen internal, telemetrie vypnutá"
  fi
done
# curl běží přímo v obou kontejnerech Brightbean; ignoruje případné HTTP proxy.
for service in jrc-bb-web jrc-bb-worker; do
  "$@" exec -T "$service" sh -c '
    curl --noproxy "*" --fail --silent --show-error --connect-timeout 3 http://jrc-bb-web:8000/health/ >/dev/null
    if curl --noproxy "*" --silent --connect-timeout 3 --max-time 4 http://1.1.1.1/ >/dev/null; then
      echo "CHYBA: curl dosáhl na internet"; exit 1
    fi
  '
  echo "OK $service: curl uvnitř funguje, ven selže"
done
