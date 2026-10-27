#!/bin/sh
# P1-B04: noční záloha; instalace ani plánování systemd není součástí skriptu.
set -eu
cd /home/honza/jr-content
mkdir -p ops/logs
exec >>"ops/logs/backup-nightly-$(date -u +%Y-%m-%d).log" 2>&1
if ! command -v flock >/dev/null 2>&1; then echo 'CHYBA: chybí flock.'; exit 1; fi
exec 9>ops/logs/backup-nightly.lock
if ! flock -n 9; then echo 'Noční záloha už běží.'; exit 1; fi
echo "=== Záloha začíná $(date -u +%FT%TZ) ==="
if JRC_PROJECT=jrc make backup-offsite; then
    echo "=== Záloha hotová $(date -u +%FT%TZ) ==="
else
    status=$?
    echo "=== Záloha selhala (kód $status) $(date -u +%FT%TZ) ==="
    exit "$status"
fi
