#!/bin/sh
# Pojistky obnovy na úrovni příkazů (F2-B09): bez běžících služeb, nic z hlavního projektu se nedotkne.
# Každý běh má vlastní jedinečný projekt a svazek; uklízí se jen po úspěšném dokončení.
set -eu
base=${JRC_PROJECT:-jrc}
# Test se nikdy nesmí dostat k hlavnímu projektu: souhlas a náhradní jméno z okolí zahodíme
# a "hlavní" projekt s přepínačem je výhradně náš testovací (JRC_TEST_MAIN_PROJECT).
unset JRC_RESTORE_MAIN JRC_TEST_MAIN_PROJECT
. scripts/backup/test-lib.sh
new_run
export JRC_BACKUP_DOCKER_FAILURE_FILE="$run_dir/docker-failed"
guard_project="jrc-${run_id}-guard"
test_main="jrc-${run_id}-main"
require_unused_project "$guard_project"
require_unused_project "$test_main"
[ ! -L backups ] || fail 'backups nesmí být odkaz.'
mkdir -p backups
fixture_root="backups/$guard_project"
mkdir "$fixture_root"
fixture="$fixture_root/zaloha"
mkdir "$fixture"
printf '{}' > "$fixture/manifest.json"
guard_volume=$guard_project-s3-data
guard_image=jrc-dev:$guard_project
volume_created=0
image_created=0
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] || [ -f "$run_dir/docker-failed" ]; then exit 1; fi
    if [ "$volume_created" = 1 ]; then
        label=$(checked_docker volume inspect --format '{{index .Labels "jrc.backup-test"}}' "$guard_volume")
        [ "$label" = "$run_id" ] || fail 'Nedoložené vlastnictví svazku.'
        checked_docker volume rm "$guard_volume" >/dev/null
    fi
    if [ "$image_created" = 1 ]; then
        current=$(checked_docker image inspect --format '{{.Id}}' "$guard_image")
        [ "$current" = "$image_id" ] || fail 'Obraz se změnil.'
        checked_docker image rm "$guard_image" >/dev/null
    fi
    # Složky vytvořené výhradně tímto během; rm není rekurzivní.
    rm "$fixture/manifest.json"
    rmdir "$fixture" "$fixture_root/bez-manifestu" "$fixture_root"
    rm -rf "$run_dir"
    exit "$status"
}
trap cleanup EXIT

fail() { printf 'CHYBA TESTU: %s\n' "$*" >&2; exit 1; }
expect_refused() {  # popis, očekávaný text, příkaz...
    description=$1; pattern=$2; shift 2
    if output=$("$@" 2>&1); then fail "$description: příkaz neměl uspět"; fi
    printf '%s' "$output" | grep -q -- "$pattern" || { printf '%s\n' "$output" >&2; fail "$description: chybí text '$pattern'"; }
    printf 'OK pojistka: %s\n' "$description"
}

# I odmítané volání musí přebít projekt a porty z MAKEFLAGS ještě před čtením Makefile.
guard_make() {
    make --no-print-directory JRC_PROJECT=jrc JRC_RESTORE_MAIN= JRC_APP_PORT="$(env_port JRC_APP_PORT)" \
        JRC_S3_PORT="$(env_port JRC_S3_PORT)" JRC_POSTGRES_PORT="$(env_port JRC_POSTGRES_PORT)" \
        JRC_BB_PORT="$(env_port JRC_BB_PORT)" "$@"
}
expect_refused 'make restore pro projekt jrc' 'zakázáno' guard_make restore BACKUP="$fixture"
expect_refused 'make restore pro projekt jrc s chybným přepínačem' 'zakázáno' guard_make restore JRC_RESTORE_MAIN=yes BACKUP="$fixture"
expect_refused 'make test-backup pro projekt jrc' 'zakázáno' guard_make test-backup
expect_refused 'restore-guard pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-guard "$fixture"
expect_refused 'restore-precheck pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-precheck "$fixture"
expect_refused 'restore-data pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-data "$fixture"
expect_refused 'restore-finish pro projekt jrc' 'hlavního projektu jrc' env JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-finish
expect_refused 'obnova bez BACKUP' 'Chybí BACKUP' env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-guard ''
expect_refused 'obnova z neexistující zálohy' 'neexistuje' env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-guard "$fixture_root/nic"
mkdir "$fixture_root/bez-manifestu"
expect_refused 'obnova z adresáře bez manifestu' 'manifest' env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-guard "$fixture_root/bez-manifestu"
expect_refused 'neplatný název projektu' 'Neplatný' env 'JRC_PROJECT=jrc;x' sh scripts/backup/backup.sh restore-guard "$fixture"
expect_refused 'JRC_BACKUP_KEEP=0' 'JRC_BACKUP_KEEP' env JRC_PROJECT="$guard_project" JRC_BACKUP_KEEP=0 sh scripts/backup/backup.sh backup

# Neprázdný svazek cílového projektu: obnova se odmítne ještě před spuštěním čehokoli.
checked_docker image inspect "jrc-dev:$base" --format '{{.Id}}' >/dev/null
checked_docker tag "jrc-dev:$base" "$guard_image"
image_id=$(checked_docker image inspect --format '{{.Id}}' "$guard_image")
image_created=1
checked_docker volume create --label "jrc.backup-test=$run_id" "$guard_volume" >/dev/null
volume_created=1
checked_docker run --rm --pull never --network none --user 0:0 --mount "type=volume,src=$guard_volume,dst=/v" "$guard_image" sh -c 'echo data > /v/neco'
expect_refused 'neprázdný svazek cíle' 'už obsahuje data' env JRC_PROJECT=$guard_project sh scripts/backup/backup.sh restore-precheck "$fixture"
checked_docker run --rm --pull never --network none --user 0:0 --mount "type=volume,src=$guard_volume,dst=/v" "$guard_image" sh -c 'rm /v/neco; echo /x > /v/.jrc-working-dir'
env JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-volume-check
printf 'OK pojistka: svazek jen s .jrc-working-dir je prázdný, kontrola nic nespouští ani nezapisuje\n'

# --- F2-B09a: obnova do hlavního projektu po havárii. Skutečný jrc se nevolá: za "hlavní" slouží $test_main
# (JRC_TEST_MAIN_PROJECT přidá chráněné jméno, `jrc` zůstává chráněný vždy). ---
main_sh() { env JRC_TEST_MAIN_PROJECT="$test_main" JRC_PROJECT="$test_main" "$@"; }
for operation in restore-guard restore-volume-check restore-precheck restore-data; do
    expect_refused "hlavní projekt bez přepínače ($operation)" 'hlavního projektu jrc' \
        main_sh sh scripts/backup/backup.sh "$operation" "$fixture"
    expect_refused "hlavní projekt se špatným přepínačem ($operation)" 'hlavního projektu jrc' \
        main_sh JRC_RESTORE_MAIN=yes sh scripts/backup/backup.sh "$operation" "$fixture"
done
expect_refused 'restore-finish pro hlavní projekt bez přepínače' 'hlavního projektu jrc' main_sh sh scripts/backup/backup.sh restore-finish
expect_refused 'neplatné JRC_TEST_MAIN_PROJECT' 'Neplatné' env JRC_TEST_MAIN_PROJECT=jrc JRC_PROJECT="$guard_project" sh scripts/backup/backup.sh restore-guard "$fixture"
# Podvržený Docker (žádné skutečné volání Dockeru): výpisy kontejnerů a svazků hlavního projektu jsou vymyšlené.
fake_bin="$run_dir/fake-bin"
mkdir "$fake_bin"
cat > "$fake_bin/docker" <<'FAKE'
#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1 $2" in
    "ps -a") [ "$FAKE_DOCKER_FAIL" = 1 ] && exit 1; printf '%s' "$FAKE_DOCKER_CONTAINERS"; exit 0 ;;
    "volume ls") printf '%s' "$FAKE_DOCKER_VOLUMES"; exit 0 ;;
    "image inspect") exit 0 ;;
    "run --rm") printf '%s' "$FAKE_DOCKER_CONTENT"; exit 0 ;;
esac
exit 99
FAKE
chmod 700 "$fake_bin/docker"
FAKE_DOCKER_LOG="$run_dir/fake-docker.log"
: > "$FAKE_DOCKER_LOG"
fake_containers=
fake_fail=0
fake_volumes=
fake_content=
fake_main() {
    env -u JRC_BACKUP_DOCKER_FAILURE_FILE PATH="$fake_bin:$PATH" FAKE_DOCKER_LOG="$FAKE_DOCKER_LOG" \
        DOCKER_HOST=unix:///nonexistent/jrc-test-no-docker.sock \
        FAKE_DOCKER_CONTAINERS="$fake_containers" FAKE_DOCKER_FAIL="$fake_fail" \
        FAKE_DOCKER_VOLUMES="$fake_volumes" FAKE_DOCKER_CONTENT="$fake_content" \
        JRC_TEST_MAIN_PROJECT="$test_main" JRC_PROJECT="$test_main" JRC_RESTORE_MAIN=yes-empty "$@"
}
fake_containers="$test_main-dev"
expect_refused 'hlavní projekt s běžícím kontejnerem' 'má kontejnery' fake_main sh scripts/backup/backup.sh restore-guard "$fixture"
expect_refused 'hlavní projekt s kontejnerem (restore-precheck)' 'má kontejnery' fake_main sh scripts/backup/backup.sh restore-precheck "$fixture"
expect_refused 'hlavní projekt s kontejnerem (restore-data)' 'má kontejnery' fake_main sh scripts/backup/backup.sh restore-data "$fixture"
fake_containers=; fake_fail=1
expect_refused 'selhání Dockeru při zjišťování kontejnerů hlavního projektu' 'Nelze zjistit kontejnery' \
    fake_main sh scripts/backup/backup.sh restore-guard "$fixture"
fake_fail=0
# Totéž se skutečným jménem hlavního projektu: Docker je podvržený a navíc DOCKER_HOST ukazuje na neexistující soket,
# takže ani při chybě v PATH by se k Dockeru nic nedostalo.
fake_containers="x-dev"
expect_refused 'jrc s přepínačem a s kontejnerem je odmítnut až kontrolou kontejnerů' 'má kontejnery' \
    fake_main env -u JRC_TEST_MAIN_PROJECT JRC_PROJECT=jrc sh scripts/backup/backup.sh restore-guard "$fixture"
fake_containers=
# Neprázdný svazek: odmítnuto i s přepínačem a bez rady na mazání.
fake_volumes="$test_main-s3-data"; fake_content=neco
expect_refused 'hlavní projekt s neprázdným svazkem i s přepínačem (restore-precheck)' 'už obsahuje data' \
    fake_main sh scripts/backup/backup.sh restore-precheck "$fixture"
expect_refused 'hlavní projekt s neprázdným svazkem i s přepínačem (restore-volume-check)' 'nikdy nemaže' \
    fake_main sh scripts/backup/backup.sh restore-volume-check
if fake_main sh scripts/backup/backup.sh restore-volume-check 2>&1 | grep -q 'down --volumes'; then
    fail 'U hlavního projektu se nesmí radit down --volumes'
fi
printf 'OK pojistka: u hlavního projektu se nikdy neradí mazání svazků\n'
# Přepínač, žádný kontejner, svazky neexistují nebo jsou prázdné: pojistky projdou (nic se nespouští).
fake_main sh scripts/backup/backup.sh restore-guard "$fixture"
fake_volumes=; fake_content=
fake_main sh scripts/backup/backup.sh restore-volume-check
fake_volumes=$(printf '%s\n%s\n' "$test_main-s3-data" "$test_main-postgres-data")
fake_main sh scripts/backup/backup.sh restore-volume-check
printf 'OK pojistka: hlavní projekt bez kontejnerů s neexistujícími či prázdnými svazky projde pojistkami\n'
# Pojistky smějí zavolat jen výpisy a čtení; žádné up, rm, volume rm, compose.
[ -s "$FAKE_DOCKER_LOG" ] || fail 'Podvržený Docker nebyl zavolán'
if grep -Ev '^(ps -a |volume ls|image inspect |run --rm --pull never --network none )' "$FAKE_DOCKER_LOG" >/dev/null; then
    grep -Ev '^(ps -a |volume ls|image inspect |run --rm --pull never --network none )' "$FAKE_DOCKER_LOG" >&2 || true
    fail 'Pojistka zavolala Docker k něčemu jinému než k čtení.'
fi
printf 'OK pojistka: pojistky obnovy hlavního projektu jen čtou\n'
rm "$fake_bin/docker" "$FAKE_DOCKER_LOG"
rmdir "$fake_bin"

# --- F2-B09a: zámek obnovy hlavního projektu (flock). Soubor zámku je testovací; pro jrc se dělá zkouška v prázdné složce. ---
test_lock="$run_dir/restore.lock"
lock_args="JRC_TEST_MAIN_PROJECT=$test_main JRC_PROJECT=$test_main JRC_TEST_RESTORE_LOCK=$test_lock"
# shellcheck disable=SC2086
env $lock_args sh scripts/restore-lock.sh check
printf 'OK pojistka: bez zámku se up/reset/down neodmítají\n'
: > "$test_lock"
# shellcheck disable=SC2086
env $lock_args sh scripts/restore-lock.sh check
printf 'OK pojistka: existující, ale nedržený zámek nevadí\n'
# Držitel zámku (deskriptor 9). Jeho vlastní kroky zámek zdědí; cizí proces (s uzavřeným deskriptorem 9) je odmítnut.
# Podshell musí zůstat naživu (žádné exec posledního příkazu), jinak by se deskriptor se zámkem zavřel.
holder() { ( exec 9>> "$test_lock"; flock -n 9 || exit 70; status=0; "$@" || status=$?; exit "$status" ); }
# shellcheck disable=SC2086
holder env $lock_args sh scripts/restore-lock.sh check
printf 'OK pojistka: vlastní kroky obnovy zámek zdědí\n'
# shellcheck disable=SC2086
expect_refused 'cizí proces při drženém zámku (check)' 'Probíhá obnova' \
    holder sh -c 'exec 9>&-; exec "$@"' sh env $lock_args sh scripts/restore-lock.sh check
# shellcheck disable=SC2086
expect_refused 'druhá obnova při drženém zámku' 'Probíhá jiná obnova' \
    holder sh -c 'exec 9>&-; exec "$@"' sh env $lock_args JRC_RESTORE_MAIN=yes-empty sh scripts/backup/backup.sh restore-run "$fixture"
for target in up down reset; do
    # shellcheck disable=SC2086
    expect_refused "make $target při drženém zámku obnovy" 'Probíhá obnova' \
        holder sh -c 'exec 9>&-; exec "$@"' sh env $lock_args make --no-print-directory JRC_PROJECT="$test_main" "$target"
done
# Skutečné jméno jrc: vlastní zámek v kořeni složky (zde prázdná dočasná složka), testovací náhrada souboru se ignoruje.
lock_dir="$run_dir/lockdir"
mkdir "$lock_dir"
repo_scripts="$PWD/scripts"
(cd "$lock_dir" && env JRC_PROJECT=jrc JRC_TEST_RESTORE_LOCK=/nonexistent/nikdy sh "$repo_scripts/restore-lock.sh" check)
jrc_lock_probe() {
    ( cd "$lock_dir"; exec 9>> .jrc-restore.lock; flock -n 9 || exit 70
      status=0
      sh -c 'exec 9>&-; exec env JRC_PROJECT=jrc JRC_TEST_RESTORE_LOCK=/nonexistent/nikdy sh "$1/restore-lock.sh" check' sh "$repo_scripts" || status=$?
      exit "$status" )
}
expect_refused 'jrc: zámek v kořeni složky se drží i při podvržené cestě' 'Probíhá obnova' jrc_lock_probe
rm "$lock_dir/.jrc-restore.lock"
rmdir "$lock_dir"
rm "$test_lock"
echo 'Všechny pojistky obnovy fungují.'
