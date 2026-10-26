#!/bin/sh
set -eu
case "$JRC_MODE" in
  prod) cd /opt/jrc-web; exec /app/node_modules/.bin/next start --hostname 0.0.0.0 ;;
  dev) exec pnpm exec next dev --hostname 0.0.0.0 ;;
  *) exit 2 ;;
esac
