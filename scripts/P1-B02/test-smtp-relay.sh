#!/bin/sh
set -eu

[ "${JRC_PROJECT:-}" = "jrc-p1b02" ] || {
    echo "Použij JRC_PROJECT=jrc-p1b02" >&2
    exit 2
}

export JRC_SMTP_HOST=jrc-smtp-test
export JRC_SMTP_PORT=2525
export JRC_SMTP_USER=local-test-user
export JRC_SMTP_PASSWORD=local-test-password
export JRC_SMTP_FROM=relay-test@example.invalid
export COMPOSE_PROFILES="smtp,smtp-test"
compose="docker compose --project-name jrc-p1b02 --file docker-compose.yml --env-file .env"
cleanup() {
    $compose stop jrc-smtp-relay jrc-smtp-test >/dev/null 2>&1 || true
    $compose rm --force jrc-smtp-relay jrc-smtp-test >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

$compose up --detach --build --wait jrc-smtp-relay jrc-smtp-test
$compose exec -T jrc-smtp-test sh -c 'rm -f /tmp/inbox-count'
$compose run --rm --no-deps \
    -e JRC_SMTP_HOST=jrc-smtp-relay \
    -e JRC_SMTP_PORT=2525 \
    -e JRC_SMTP_USER=local-test-user \
    -e JRC_SMTP_PASSWORD=local-test-password \
    -e JRC_SMTP_FROM=relay-test@example.invalid \
    -e DJANGO_SETTINGS_MODULE=config.settings.jrc \
    jrc-bb-web python manage.py shell -c \
    'from django.core.mail import EmailMessage; from django.test import override_settings; m=EmailMessage("P1-B02 relay regression", "local only", to=["recipient@example.invalid"]); c=override_settings(EMAIL_USE_TLS=False); c.enable(); assert m.send() == 1; c.disable()'

count=$($compose exec -T jrc-smtp-test sh -c 'cat /tmp/inbox-count')
[ "$count" = 1 ] || { echo "SMTP relay delivered $count messages; expected 1" >&2; exit 1; }
echo "OK: Brightbean sent one message through the fixed-destination relay to the local SMTP container."
cleanup
trap - EXIT HUP INT TERM
