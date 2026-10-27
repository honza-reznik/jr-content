#!/bin/sh
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM
log="$tmpdir/docker.log"

cat >"$tmpdir/docker" <<'MOCK'
#!/bin/sh
printf '%s\n' "$*" >>"$JRC_DOCKER_LOG"
case " $* " in
    *" cat /tmp/inbox-count "*) printf '1\n' ;;
esac
MOCK
chmod +x "$tmpdir/docker"

JRC_PROJECT=jrc JRC_DOCKER_LOG="$log" PATH="$tmpdir:$PATH" \
    sh "$repo/scripts/P1-B02/test-smtp-relay.sh" >/dev/null

grep -Eq -- '--project-name jrc-smtp-test-[0-9]+' "$log"
grep -Eq -- 'down --remove-orphans --rmi local$' "$log"
if grep -Eq -- 'down .* (-v|--volumes)([[:space:]]|$)|--project-name jrc([[:space:]]|$)|jrc-bb-web|jrc-postgres|jrc-s3' "$log"; then
    echo "SMTP relay test touched the main Compose project, application services, or volume deletion" >&2
    exit 1
fi

echo "OK: SMTP relay test uses an isolated temporary project for JRC_PROJECT=jrc and never deletes volumes."
