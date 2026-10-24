#!/bin/sh
# Pojistky obnovy na úrovni příkazů (F2-B09): bez běžících služeb, nic z hlavního projektu se nedotkne.
# Dočasný projekt jrc-guardtest má jen jeden svazek s kontrolním souborem; po testu se odstraní.
set -eu
base=${JRC_PROJECT:-jrc}
tmp=$(mktemp -d)
fixture="$tmp/zaloha"
mkdir "$fixture" && printf '{}' > "$fixture/manifest.json"
guard_project=jrc-guardtest
guard_volume=$guard_project-s3-data
guard_image=jrc-dev:$guard_project
cleanup() {
    docker volume rm "$guard_volume" >/dev/null 2>&1 || true
    docker image rm "$guard_image" >/dev/null 2>&1 || true
    rm -rf "$tmp"
}
trap cleanup EXIT

fail() { printf 'CHYBA TESTU: %s\n' "$*" >&2; exit 1; }
expect_refused() {  # popis, očekávaný text, příkaz...
    description=$1; pattern=$2; shift 2
    if output=$("$@" 2>&1); then fail "$description: příkaz neměl uspět"; fi
    printf '%s' "$output" | grep -q -- "$pattern" || { printf '%s\n' "$output" >&2; fail "$description: chybí text '$pattern'"; }
    printf 'OK pojistka: %s\n' "$description"
}

expect_refused 'make restore pro projekt jrc' 'zakázáno' env JRC_PROJECT=jrc make --no-print-directory restore BACKUP="$fixture"
expect_refused 'make test-backup pro projekt jrc' 'zakázáno' env JRC_PROJECT=jrc make --no-print-directory test-backup
expect_refused 'restore-guard pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-guard "$fixture"
expect_refused 'restore-precheck pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-precheck "$fixture"
expect_refused 'restore-data pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-data "$fixture"
expect_refused 'restore-finish pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-finish
expect_refused 'obnova bez BACKUP' 'Chybí BACKUP' env JRC_PROJECT=jrc-guardtest sh scripts/backup/backup.sh restore-guard ''
expect_refused 'obnova z neexistující zálohy' 'neexistuje' env JRC_PROJECT=jrc-guardtest sh scripts/backup/backup.sh restore-guard "$tmp/nic"
mkdir "$tmp/bez-manifestu"
expect_refused 'obnova z adresáře bez manifestu' 'manifest' env JRC_PROJECT=jrc-guardtest sh scripts/backup/backup.sh restore-guard "$tmp/bez-manifestu"
expect_refused 'neplatný název projektu' 'Neplatný' env 'JRC_PROJECT=jrc;x' sh scripts/backup/backup.sh restore-guard "$fixture"
expect_refused 'JRC_BACKUP_KEEP=0' 'JRC_BACKUP_KEEP' env JRC_PROJECT=jrc-guardtest JRC_BACKUP_KEEP=0 sh scripts/backup/backup.sh backup

# Neprázdný svazek cílového projektu: obnova se odmítne ještě před spuštěním čehokoli.
docker image inspect "jrc-dev:$base" >/dev/null 2>&1 || fail "chybí obraz jrc-dev:$base"
docker tag "jrc-dev:$base" "$guard_image"
docker volume create "$guard_volume" >/dev/null
docker run --rm --pull never --network none --user 0:0 --mount "type=volume,src=$guard_volume,dst=/v" "$guard_image" sh -c 'echo data > /v/neco'
expect_refused 'neprázdný svazek cíle' 'už obsahuje data' env JRC_PROJECT=$guard_project sh scripts/backup/backup.sh restore-precheck "$fixture"
docker run --rm --pull never --network none --user 0:0 --mount "type=volume,src=$guard_volume,dst=/v" "$guard_image" sh -c 'rm /v/neco; echo /x > /v/.jrc-working-dir'
if output=$(env JRC_PROJECT=$guard_project sh scripts/backup/backup.sh restore-precheck "$fixture" 2>&1); then fail 'prázdný svazek: nesprávný manifest měl projít jen přes pojistku svazku, ne celou kontrolou'; fi
printf '%s' "$output" | grep -q 'už obsahuje data' && fail 'svazek s jediným .jrc-working-dir se má považovat za prázdný'
printf '%s' "$output" | grep -q 'CHYBA' || fail 'po kontrole svazku měla následovat kontrola zálohy'
printf 'OK pojistka: svazek jen s .jrc-working-dir je prázdný, neplatná záloha se odmítne až kontrolou manifestu\n'
echo 'Všechny pojistky obnovy fungují.'
