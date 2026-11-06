#!/usr/bin/env bash
set -euo pipefail
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
cp /source/scripts/build/download-guard.sh "$temporary/guard.sh"
cd "$temporary"
export JRC_PROJECT=jrc-guard-test JRC_BUILD_INTERFACE=lo
unset JRC_BUILD_GUARD_ACTIVE JRC_BUILD_GUARD_PID JRC_BUILD_CACHE_FILL
bash guard.sh true
if bash guard.sh bash -c 'exit 42'; then exit 1; else [[ $? == 42 ]]; fi
if JRC_BUILD_DOWNLOAD_LIMIT_MB=invalid bash guard.sh true; then exit 1; fi
# Simulovaný čítač přesáhne limit během běhu. Žádná skutečná síť ani Docker.
mkdir bin
cat >bin/cat <<'MOCK'
#!/usr/bin/env bash
if [[ $1 == /sys/class/net/lo/statistics/rx_bytes ]]; then
    if [[ -n ${TEST_RX_VALUE:-} ]]; then
        /bin/cat "$TEST_RX_VALUE"
    elif [[ -f "$TEST_COUNTER" ]]; then echo 2000001; else touch "$TEST_COUNTER"; echo 0; fi
else
    /bin/cat "$@"
fi
MOCK
chmod +x bin/cat
export TEST_COUNTER="$temporary/counter"
if PATH="$temporary/bin:$PATH" JRC_BUILD_DOWNLOAD_LIMIT_MB=1 \
    bash guard.sh bash -c 'sleep 4; touch escaped'; then
    echo 'Hlídač nepřerušil překročený limit' >&2; exit 1
else
    [[ $? == 125 ]]
fi
[[ ! -e escaped ]]
grep -q 'LIMIT project=jrc-guard-test' ops/logs/build-download.log
grep -q 'status=125' ops/logs/build-download.log
# Režim prvního naplnění skutečně použije vlastní vyšší limit.
rm "$TEST_COUNTER"
PATH="$temporary/bin:$PATH" JRC_BUILD_DOWNLOAD_LIMIT_MB=1 \
    JRC_BUILD_CACHE_FILL=1 JRC_BUILD_CACHE_FILL_LIMIT_MB=3 bash guard.sh sleep 0.4
grep -q 'limit_MB=3' ops/logs/build-download.log
# Volný příznak ani PID běžného rodiče nesmějí vypnout měření.
for forged in 1 "$$" 99999999; do
    rm -f "$TEST_COUNTER" escaped
    if PATH="$temporary/bin:$PATH" JRC_BUILD_DOWNLOAD_LIMIT_MB=1 \
        JRC_BUILD_GUARD_ACTIVE=1 JRC_BUILD_GUARD_PID=$forged \
        bash guard.sh bash -c 'sleep 4; touch escaped'; then exit 1; else [[ $? == 125 ]]; fi
    [[ ! -e escaped ]]
done
if JRC_BUILD_GUARD_ACTIVE=1 JRC_BUILD_GUARD_PID=$$ bash guard.sh --check-parent; then exit 1; fi
# Skutečný nadřazený hlídač zachová skupinu i návratovou hodnotu vnoření.
bash guard.sh bash -c 'bash guard.sh --check-parent; bash guard.sh bash -c "exit 42"' && exit 1 || [[ $? == 42 ]]
cat > pgid.sh <<'PGID'
set -eu
read -r pid comm state ppid pgid rest < /proc/$$/stat
printf '%s\n' "$pgid" > "$1"
PGID
bash guard.sh bash -ec 'bash pgid.sh outer.pgid; bash guard.sh bash pgid.sh nested.pgid; cmp outer.pgid nested.pgid'
rm -f "$TEST_COUNTER" escaped
if PATH="$temporary/bin:$PATH" JRC_BUILD_DOWNLOAD_LIMIT_MB=1 \
    bash guard.sh bash guard.sh bash -c 'sleep 4; touch escaped'; then exit 1; else [[ $? == 125 ]]; fi
[[ ! -e escaped ]]
# Limit dávky se nesmí obnovit začátkem vnořeného sestavení: 0,6 + 0,6 MB.
export TEST_RX_VALUE="$temporary/rx-value"
printf 0 > "$TEST_RX_VALUE"
if PATH="$temporary/bin:$PATH" JRC_BUILD_DOWNLOAD_LIMIT_MB=1 \
    bash guard.sh bash -ec 'echo 600000 > "$TEST_RX_VALUE"; bash guard.sh bash -ec '\''echo 1200000 > "$TEST_RX_VALUE"; sleep 4; touch escaped'\'''; then
    exit 1
else
    [[ $? == 125 ]]
fi
[[ ! -e escaped ]]
echo 'OK: měření, limity, podvržené prostředí, skutečné vnoření a ukončení skupiny.'
