#!/bin/sh
# P1-B04: hostitel spouští zálohu, připraví vzdálenou historii a případně pushne přímo na URL.
set -u
root=${JRC_BACKUP_NIGHTLY_ROOT:-/home/honza/jr-content}
project=${JRC_BACKUP_NIGHTLY_PROJECT:-jrc}
cd "$root"
mkdir -p ops/logs
exec >>"ops/logs/backup-nightly-$(date -u +%Y-%m-%d).log" 2>&1

status() { printf '%s\n' "$1"; }
if ! command -v flock >/dev/null 2>&1; then status 'CHYBA: flock nedostupný (kód 1).'; exit 1; fi
exec 9>ops/logs/backup-nightly.lock
if ! flock -n 9; then status 'CHYBA: záloha již běží (kód 1).'; exit 1; fi

remote=$(sed -n 's/^[[:space:]]*JRC_BACKUP_GIT_REMOTE[[:space:]]*=[[:space:]]*//p' .env | tail -n 1)
repo=$(sed -n 's/^[[:space:]]*JRC_BACKUP_GIT_DIR[[:space:]]*=[[:space:]]*//p' .env | tail -n 1)
[ -n "$repo" ] || repo=/home/honza/jr-content-backup-git
case "$repo" in /*) ;; *) repo="$root/$repo" ;; esac

if [ -n "$remote" ]; then
    if [ ! -d "$repo/.git" ]; then
        if [ -e "$repo" ] && [ -n "$(find "$repo" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
            status 'CHYBA: složka vzdálených záloh není git klon (kód 1).'
            exit 1
        fi
        mkdir -p "$(dirname "$repo")"
        if git clone --quiet "$remote" "$repo" >/dev/null 2>&1; then :; else
            result=$?; status "CHYBA: příprava git klonu selhala (kód $result)."; exit "$result"
        fi
    fi
    if ! git -C "$repo" diff --quiet >/dev/null 2>&1 || ! git -C "$repo" diff --cached --quiet >/dev/null 2>&1; then
        status 'CHYBA: pracovní složka vzdálených záloh obsahuje změny (kód 1).'
        exit 1
    fi
    if git ls-remote --exit-code "$remote" refs/heads/zalohy >/dev/null 2>&1; then
        if git -C "$repo" fetch --quiet "$remote" refs/heads/zalohy:refs/remotes/jrc-offsite/zalohy >/dev/null 2>&1; then :; else
            result=$?; status "CHYBA: načtení vzdálené větve selhalo (kód $result)."; exit "$result"
        fi
        if git -C "$repo" show-ref --verify --quiet refs/heads/zalohy; then
            if git -C "$repo" switch --quiet zalohy >/dev/null 2>&1 && \
               git -C "$repo" merge --ff-only --quiet refs/remotes/jrc-offsite/zalohy >/dev/null 2>&1; then :; else
                result=$?; status "CHYBA: místní větev záloh nejde bezpečně navázat (kód $result)."; exit "$result"
            fi
        elif git -C "$repo" switch --quiet -c zalohy refs/remotes/jrc-offsite/zalohy >/dev/null 2>&1; then :; else
            result=$?; status "CHYBA: příprava místní větve záloh selhala (kód $result)."; exit "$result"
        fi
    fi
fi

if JRC_PROJECT="$project" make backup-offsite >/dev/null 2>&1; then
    status 'Záloha: připravena.'
else
    result=$?; status "CHYBA: příprava zálohy selhala (kód $result)."; exit "$result"
fi

if [ -z "$remote" ]; then
    status 'Push: vynechán, vzdálená adresa není nastavena.'
    exit 0
fi
if git -C "$repo" push "$remote" zalohy >/dev/null 2>&1; then
    status 'Push: dokončen.'
else
    result=$?; status "CHYBA: push selhal (kód $result)."; exit "$result"
fi
