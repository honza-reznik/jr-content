#!/bin/sh
# Hostitel jen řídí Docker/inspect; síťové zkoušky běží v kontejnerech.
set -eu
for service in $("$@" config --services); do
  case "$service" in jrc-dev|jrc-ff-gate) continue ;; esac
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
# Brána smí publikovat pouze místní porty, nikdy 0.0.0.0/::.
gate=$("$@" ps --quiet jrc-ff-gate)
test -n "$gate"
bindings=$(docker inspect --format '{{range .HostConfig.PortBindings}}{{range .}}{{.HostIp}}{{"\n"}}{{end}}{{end}}' "$gate")
test -n "$bindings"
test -z "$(printf '%s\n' "$bindings" | sed '/^127\.0\.0\.1$/d')"
echo 'OK brána: všechny publikované porty pouze 127.0.0.1'
