#!/bin/sh
set -eu
case "$JRC_MODE" in
  prod)
    python manage.py collectstatic --noinput --verbosity 0
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 120 --forwarded-allow-ips '' ;;
  dev) exec python manage.py runserver 0.0.0.0:8000 ;;
  *) echo 'JRC_MODE musí být prod nebo dev' >&2; exit 2 ;;
esac
