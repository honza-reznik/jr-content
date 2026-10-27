#!/bin/sh
set -eu

project=${JRC_PROJECT:-jrc}
case "$project" in
    jrc) ;;
    jrc-*)
        suffix=${project#jrc-}
        case "$suffix" in
            ""|*[!a-z0-9-]*) echo "Neplatný JRC_PROJECT: $project" >&2; exit 2 ;;
        esac
        ;;
    *) echo "Neplatný JRC_PROJECT: $project" >&2; exit 2 ;;
esac

export JRC_SMTP_HOST=jrc-smtp-test
export JRC_SMTP_PORT=2525
export JRC_SMTP_USER=local-test-user
export JRC_SMTP_PASSWORD=local-test-password
export JRC_SMTP_FROM=relay-test@example.invalid
export COMPOSE_PROFILES="smtp,smtp-test"
compose_project="${project}-smtp-test-$$"
compose() {
    JRC_PROJECT="$compose_project" docker compose \
        --project-name "$compose_project" --file docker-compose.yml --env-file .env "$@"
}
cleanup() {
    compose down --remove-orphans --rmi local >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

compose up --detach --build --wait jrc-smtp-relay jrc-smtp-test
compose exec -T jrc-smtp-test sh -c 'rm -f /tmp/inbox-count'
compose exec -T jrc-smtp-relay python -c \
    'from email.message import EmailMessage; import smtplib; m=EmailMessage(); m["Subject"]="P1-B02 relay regression"; m["From"]="relay-test@example.invalid"; m["To"]="recipient@example.invalid"; m.set_content("local only"); s=smtplib.SMTP("127.0.0.1", 2525, timeout=5); s.login("local-test-user", "local-test-password"); s.send_message(m); s.quit()'

count=$(compose exec -T jrc-smtp-test sh -c 'cat /tmp/inbox-count')
[ "$count" = 1 ] || { echo "SMTP relay delivered $count messages; expected 1" >&2; exit 1; }
echo "OK: SMTP client sent one message through the fixed-destination relay to the local SMTP container."
cleanup
trap - EXIT HUP INT TERM
