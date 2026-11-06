#!/usr/bin/env bash
set -euo pipefail
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
cp /source/scripts/build/download-guard.sh "$temporary/guard.sh"
cd "$temporary"
export JRC_PROJECT=jrc-guard-test JRC_BUILD_INTERFACE=lo
unset JRC_BUILD_GUARD_ACTIVE JRC_BUILD_CACHE_FILL
bash guard.sh true
if bash guard.sh bash -c 'exit 42'; then exit 1; else [[ $? == 42 ]]; fi
if JRC_BUILD_DOWNLOAD_LIMIT_MB=invalid bash guard.sh true; then exit 1; fi
# Simulovaný čítač přesáhne limit během běhu. Žádná skutečná síť ani Docker.
mkdir bin
cat >bin/cat <<'MOCK'
#!/usr/bin/env bash
if [[ $1 == /sys/class/net/lo/statistics/rx_bytes ]]; then
    if [[ -f "$TEST_COUNTER" ]]; then echo 2000001; else touch "$TEST_COUNTER"; echo 0; fi
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
# Vnořená měření zachovají návratovou hodnotu a nemění skupinu procesů.
JRC_BUILD_GUARD_ACTIVE=1 bash guard.sh true
echo 'OK: měření, limity, přerušení skupiny procesů, naplnění a návratové kódy.'
