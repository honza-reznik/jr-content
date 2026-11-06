#!/bin/sh
# AK1: řetězec Funnel/Serve (https) -> Caddy -> Brightbean. Jen projekt jrc-p1b29; .env se vždy vrátí.
# Token klíče je v ignorovaném ops/test-output/P1-B29/token (vydává mkkey.py), nikdy se nevypisuje.
set -eu
[ "${JRC_PROJECT:-}" = jrc-p1b29 ] || exit 2
compose() { docker compose --project-name jrc-p1b29 --file docker-compose.yml --env-file .env "$@"; }
T=$(cat ops/test-output/P1-B29/token)
port=13713
fail=0
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
expect() { # popis očekávaný skutečný
  if [ "$2" = "$3" ]; then printf 'OK   %s: %s\n' "$1" "$3"; else printf 'CHYBA %s: čekáno %s, je %s\n' "$1" "$2" "$3"; fail=1; fi
}
internal() { # odesílatel z vnitřní sítě (nedůvěryhodný pro Caddy); $1 = hodnota XFP nebo prázdné
  docker exec -i jrc-p1b29-dev python3 - "$T" "$1" <<'PY'
import sys, urllib.request, urllib.error
h = {"Authorization": "Bearer " + sys.argv[1], "Host": "127.0.0.1:13713"}
if sys.argv[2]: h["X-Forwarded-Proto"] = sys.argv[2]
try: print(urllib.request.urlopen(urllib.request.Request("http://jrc-proxy:8080/jrc/api/v1/me/", headers=h), timeout=15).status, end="")
except urllib.error.HTTPError as e: print(e.code, end="")
PY
}
U=http://127.0.0.1:$port/jrc/api/v1/me/
A="Authorization: Bearer $T"
mcp() { code -X POST -H "$A" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' "$@" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' http://127.0.0.1:$port/jrc/api/v1/mcp/; }
forbidden() { # zakázané cesty musí vracet 404 i přes nové handle bloky (pořadí direktiv Caddy)
  for path in /jrc/admin /jrc/admin/ /jrc/admin/login/ /jrc/video-api/docs /jrc/video-api/openapi.json /jrc/video-api/admin/ /jrc/video-api/setup/ /jrc/video/setup / /nic; do
    expect "zakázáno $path" 404 "$(code http://127.0.0.1:$port$path)"
    expect "zakázáno $path s klíčem i https hlavičkou" 404 "$(code -H "$A" -H 'X-Forwarded-Proto: https' http://127.0.0.1:$port$path)"
  done
  expect "storage GET" 404 "$(code http://127.0.0.1:$port/jrc/storage/jrc-video/x.mp4)"
}
echo "== http původ (testovací prostředí jrc-t)"
expect "z hostitele bez hlavičky" 401 "$(code -H "$A" $U)"
expect "z hostitele s X-Forwarded-Proto https (jak testovat)" 200 "$(code -H "$A" -H 'X-Forwarded-Proto: https' $U)"
expect "z hostitele s X-Forwarded-Proto http" 401 "$(code -H "$A" -H 'X-Forwarded-Proto: http' $U)"
expect "MCP z hostitele s https hlavičkou" 200 "$(mcp -H 'X-Forwarded-Proto: https')"
expect "špatný klíč i s https hlavičkou" 401 "$(code -H 'Authorization: Bearer bb_studio_neplatny_00000000' -H 'X-Forwarded-Proto: https' $U)"
expect "podvržená hlavička z vnitřní sítě (cizí odesílatel)" 401 "$(internal https)"
forbidden
cp -p .env ops/test-output/P1-B29/env.before
cleanup() { r=$?; trap - EXIT HUP INT TERM; mv ops/test-output/P1-B29/env.before .env; compose up --detach --no-build --pull never --force-recreate --wait --wait-timeout 180 jrc-bb-web jrc-proxy >/dev/null 2>&1 || r=1; exit "$r"; }
trap cleanup EXIT HUP INT TERM
sed -i "s|^JRC_PUBLIC_ORIGIN=.*|JRC_PUBLIC_ORIGIN=https://127.0.0.1:$port|" .env
compose up --detach --no-build --pull never --force-recreate --wait --wait-timeout 180 jrc-bb-web jrc-proxy >/dev/null 2>&1
echo "== https původ (skutečné nasazení: Funnel ukončuje TLS, Caddy vidí http)"
expect "bez hlavičky X-Forwarded-Proto (Funnel ji nemusí poslat)" 200 "$(code -H "$A" $U)"
expect "MCP bez hlavičky" 200 "$(mcp)"
expect "s X-Forwarded-Proto http (podvrh jako http nic nezmění)" 200 "$(code -H "$A" -H 'X-Forwarded-Proto: http' $U)"
expect "z vnitřní sítě bez hlavičky (mez: při https původu je schéma vnuceno všem odesílatelům uvnitř stroje)" 200 "$(internal '')"
expect "z vnitřní sítě s X-Forwarded-Proto http (mez, stejně)" 200 "$(internal http)"
forbidden
expect "špatný klíč" 401 "$(code -H 'Authorization: Bearer bb_studio_neplatny_00000000' $U)"
expect "bez klíče" 401 "$(code $U)"
exit "$fail"
