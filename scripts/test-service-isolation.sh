#!/bin/sh
# Hostitel jen řídí Docker/inspect; síťové zkoušky běží v kontejnerech.
set -eu
for service in $("$@" config --services); do
  case "$service" in jrc-dev|jrc-ff-gate|jrc-proxy) continue ;; esac
  ids=$("$@" ps --all --quiet "$service")
  test -n "$ids" || { echo "Chybí služba $service"; exit 1; }
  for container in $ids; do
    networks=$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$container")
    test -n "$networks"
    for network in $networks; do
      test "$(docker network inspect --format '{{.Internal}}' "$network")" = true || {
        echo "CHYBA: $service má síť s internetem: $network"; exit 1;
      }
    done
    test "$(docker inspect --format '{{len .HostConfig.PortBindings}}' "$container")" = 0 || {
      echo "CHYBA: $service publikuje vlastní port"; exit 1;
    }
    if [ "$(docker inspect --format '{{.State.Running}}' "$container")" = true ]; then
      docker run --rm --pull never --network "container:$container" \
        --user "$(id -u):$(id -g)" --volume "$PWD/scripts/probe-isolation.cjs:/probe.cjs:ro" \
        jrc-dev:local node /probe.cjs
      echo "OK $service: jen internal, bez portů, IPv4 i IPv6 ven zablokované"
    else
      test "$(docker inspect --format '{{.State.ExitCode}}' "$container")" = 0
      echo "OK $service: dokončená inicializace, jen internal, bez portů"
    fi
  done
done
# Oba vstupy smějí publikovat pouze místní porty, nikdy 0.0.0.0/::.
for service in jrc-ff-gate jrc-proxy; do
  gate=$("$@" ps --quiet "$service")
  test -n "$gate"
  bindings=$(docker inspect --format '{{range .HostConfig.PortBindings}}{{range .}}{{.HostIp}}{{"\n"}}{{end}}{{end}}' "$gate")
  test -n "$bindings"
  test -z "$(printf '%s\n' "$bindings" | sed '/^127\.0\.0\.1$/d')"
  if [ "$service" = jrc-ff-gate ]; then
    ports=$(docker inspect --format '{{range $port, $_ := .HostConfig.PortBindings}}{{$port}} {{end}}' "$gate")
    test "$ports" = '13900/tcp 15432/tcp ' || {
      echo 'CHYBA: brána smí vystavit jen S3 a PostgreSQL'; exit 1;
    }
  fi
  echo "OK $service: všechny publikované porty pouze 127.0.0.1"
done
dev=$("$@" ps --quiet jrc-dev)
test -n "$dev"
test "$(docker inspect --format '{{len .HostConfig.PortBindings}}' "$dev")" = 0
echo 'OK jrc-dev: bez publikovaných portů'
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
