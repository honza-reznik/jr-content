#!/bin/sh
set -eu
case "$JRC_MODE" in prod) workers=2 ;; dev) workers=1 ;; *) exit 2 ;; esac
# Hlavičky ověřuje aplikační middleware proti adrese jrc-proxy.
exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --workers "$workers" --no-proxy-headers --root-path "$JRC_BASE_PATH/video-api"
