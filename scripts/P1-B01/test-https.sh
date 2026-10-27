#!/bin/sh
# Test mění nastavení pouze jrc-p1b01 a po sobě vždy obnoví původní .env.
set -eu
case "${JRC_PROJECT:-}" in
  jrc-p1b01) tls_port=14163 ;;
  jrc-p1b03) tls_port=14183 ;;
  *) exit 2 ;;
esac
compose() { docker compose --project-name "$JRC_PROJECT" --file docker-compose.yml --env-file .env "$@"; }
mkdir -p .cache
cp -p .env .cache/p1b01-before-https.env
cleanup() {
  result=$?
  trap - EXIT HUP INT TERM
  docker rm -f "$JRC_PROJECT-https" >/dev/null 2>&1 || true
  mv .cache/p1b01-before-https.env .env
  compose up --detach --no-build --wait --wait-timeout 120 >/dev/null 2>&1 || result=1
  exit "$result"
}
trap cleanup EXIT HUP INT TERM
# Nezobrazovat .env ani jeho záložní kopii (obsahuje tajemství).
sed -i "s|^JRC_PUBLIC_ORIGIN=.*|JRC_PUBLIC_ORIGIN=https://127.0.0.1:$tls_port|" .env
compose up --detach --no-build --wait --wait-timeout 120
compose stop jrc-ff-gate
docker run --detach --name "$JRC_PROJECT-https" --pull never --network host \
  --env-file .env --user "$(id -u):$(id -g)" --volume "$PWD/scripts/P1-B01:/tests:ro" \
  jrc-dev:"$JRC_PROJECT" node /tests/https-proxy.cjs >/dev/null
# Chromium/FFmpeg i HTTP klienti zůstávají v Dockeru.
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --volume "$PWD:/jrc" --workdir /jrc \
  jrc-dev:"$JRC_PROJECT" node scripts/P1-B01/check-https.cjs
JRC_E2E_HTTPS=1 make test-e2e
