#!/usr/bin/env bash
# Vytvoří nastavení auditního stacku Brightbean (F1-B01) s náhodnými hesly.
# Soubor leží v audit/ (mimo git). Existující soubor nepřepíše, aby hesla seděla k uložené databázi.
set -euo pipefail

root="$(cd "$(dirname "$0")/../../.." && pwd)"
out="$root/audit/jrc-audit-brightbean.env"

if [[ -f "$out" ]]; then
  echo "Nastavení už existuje: $out (nepřepisuji)."
  exit 0
fi

rnd() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
db_pw="$(rnd)"

umask 077
cat >"$out" <<EOF
# Auditní stack Brightbean (F1-B01). Vygenerováno skriptem priprav-env.sh. Nesdílet, necommitovat.
SECRET_KEY=$(rnd)
ENCRYPTION_KEY_SALT=$(rnd)
DEBUG=true
ALLOWED_HOSTS=localhost,127.0.0.1,app
APP_URL=http://127.0.0.1:18010
SIGNUP_MODE=open
POSTGRES_PASSWORD=$db_pw
DATABASE_URL=postgres://postgres:$db_pw@postgres:5432/brightbean
STORAGE_BACKEND=local
MEDIA_ROOT=/app/media
SERVE_MEDIA=true
# E-maily se jen vypisují do logu kontejneru (nic neodchází ven).
EMAIL_BACKEND_TYPE=console
DEFAULT_FROM_EMAIL=noreply@localhost
# Všechny klíče sociálních sítí, Google, Unsplash, Sentry a Intelligence záměrně prázdné.
EOF
echo "Vytvořeno: $out"
