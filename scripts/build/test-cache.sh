#!/usr/bin/env bash
# Testuje sdílení a zámek místní cache, Docker nahrazuje simulace bez sítě.
set -euo pipefail
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
unset JRC_BUILD_CACHE_DIR JRC_BUILD_GUARD_PID JRC_BUILD_CACHE_FILL
mkdir -p "$temporary/repo/scripts/build" "$temporary/repo/docker" "$temporary/bin"
cp /source/scripts/build/{cache,images,download-guard}.sh "$temporary/repo/scripts/build/"
cp /source/Makefile "$temporary/repo/Makefile"
cd "$temporary/repo"
git init -q
git -c user.name=Test -c user.email=test@example.invalid -c commit.gpgsign=false commit -q --allow-empty -m fixture
git worktree add -q --detach "$temporary/second"
mkdir -p "$temporary/second/scripts"
cp -a scripts/build "$temporary/second/scripts/"
first=$(bash scripts/build/cache.sh)
second=$(cd "$temporary/second"; bash scripts/build/cache.sh)
[[ $first == "$second" && $first == "$temporary/repo/.cache/buildkit" ]]
cd "$temporary/second"
mkdir -p .cache/buildkit/dev/blobs/sha256
printf 'fixture blob' > .cache/buildkit/dev/blobs/sha256/test
printf '{"test":"fixture index"}\n' > .cache/buildkit/dev/index.json
(cd "$temporary/repo"; bash scripts/build/cache.sh --prepare)
cmp .cache/buildkit/dev/index.json "$first/dev/index.json"
cmp .cache/buildkit/dev/blobs/sha256/test "$first/dev/blobs/sha256/test"
# Opakované převzetí nepřepíše novější sdílený index.
printf 'new index' > "$first/dev/index.json"
(cd "$temporary/repo"; bash scripts/build/cache.sh --prepare)
[[ $(cat "$first/dev/index.json") == 'new index' ]]
# Zámek sdíleného kořene zadrží i druhý worktree.
(
    exec {holder_lock}>"$first/build.lock"
    flock "$holder_lock"
    touch "$temporary/locked"
    sleep 1
    touch "$temporary/released"
) &
holder=$!
for _ in {1..100}; do [[ ! -f $temporary/locked ]] || break; sleep 0.02; done
[[ -f $temporary/locked ]]
(cd "$temporary/repo"; bash scripts/build/cache.sh --prepare)
[[ -f $temporary/released ]]
wait "$holder"
JRC_BUILD_CACHE_DIR="$temporary/custom" bash scripts/build/cache.sh --prepare
cmp .cache/buildkit/dev/index.json "$temporary/custom/dev/index.json"

# Přímé interní cíle odmítnou podvržené prostředí před voláním Dockeru.
cd "$temporary/repo"
touch docker/Caddyfile .env
mkdir -p scripts/P1-B02
printf '#!/bin/sh\nexit 0\n' > scripts/restore-lock.sh
cat > "$temporary/bin/docker" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TEST_DOCKER_LOG"
# Síťový přenos simuluje pouze soubor, žádný Docker ani build neběží.
printf '2000001' > "$TEST_COUNTER"
sleep 4
touch "$TEST_ESCAPED"
MOCK
cat > "$temporary/bin/cat" <<'MOCK'
#!/usr/bin/env bash
if [[ $1 == /sys/class/net/lo/statistics/rx_bytes ]]; then
    /bin/cat "$TEST_COUNTER"
else
    /bin/cat "$@"
fi
MOCK
chmod +x "$temporary/bin/"*
export PATH="$temporary/bin:$PATH" JRC_PROJECT=jrc-guard-test JRC_BUILD_INTERFACE=lo
export TEST_COUNTER="$temporary/counter" TEST_DOCKER_LOG="$temporary/docker.log" TEST_ESCAPED="$temporary/escaped"
export JRC_BUILD_GUARD_ACTIVE=1 JRC_BUILD_GUARD_PID=$$ JRC_BUILD_DOWNLOAD_LIMIT_MB=1
for target in test-up-internal up-internal build-all-internal build-ff-internal runtime-images-internal; do
    if make --no-print-directory "$target" > "$temporary/make.log" 2>&1; then
        echo "Interní cíl $target obešel hlídač" >&2; exit 1
    fi
    grep -q 'skutečného nadřazeného hlídače' "$temporary/make.log"
done
[[ ! -e $TEST_DOCKER_LOG ]]
printf 0 > "$TEST_COUNTER"
if bash scripts/build/images.sh dev; then exit 1; else [[ $? == 125 ]]; fi
[[ ! -e $TEST_ESCAPED ]]
grep -q -- "--cache-from type=local,src=$first/dev" "$TEST_DOCKER_LOG"
grep -q -- "--cache-to type=local,dest=$first/dev,mode=max" "$TEST_DOCKER_LOG"
grep -q 'status=125' ops/logs/build-download.log

# Dva skutečné spouštěče z různých worktree, tentýž export dev; jen Docker
# je simulovaný. První běh držíme uvnitř importu/exportu, druhý musí čekat.
cat > "$temporary/bin/docker" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
mkdir "$TEST_BUILD_ACTIVE"
trap 'rmdir "$TEST_BUILD_ACTIVE"' EXIT
printf 'start %s\n' "$TEST_RUN_ID" >> "$TEST_BUILD_EVENTS"
cache=
while (( $# )); do
    if [[ $1 == --cache-to ]]; then
        cache=${2#type=local,dest=}
        cache=${cache%,mode=max}
    fi
    shift
done
[[ $cache == "$TEST_CACHE_EXPORT" ]]
if [[ $TEST_RUN_ID == second ]]; then
    [[ $(cat "$cache/index.json") == 'complete first' ]]
fi
printf 'incomplete %s\n' "$TEST_RUN_ID" > "$cache/index.json"
touch "$TEST_BUILD_MARKERS/started-$TEST_RUN_ID"
if [[ $TEST_RUN_ID == first ]]; then
    until [[ -f $TEST_BUILD_MARKERS/release ]]; do sleep 0.02; done
fi
printf 'complete %s\n' "$TEST_RUN_ID" > "$cache/index.json"
printf 'end %s\n' "$TEST_RUN_ID" >> "$TEST_BUILD_EVENTS"
exit "${TEST_BUILD_EXIT:-0}"
MOCK
export TEST_BUILD_ACTIVE="$temporary/build-active" TEST_BUILD_EVENTS="$temporary/build-events"
export TEST_BUILD_MARKERS="$temporary/build-markers" TEST_CACHE_EXPORT="$first/dev"
export JRC_BUILD_DOWNLOAD_LIMIT_MB=500
printf 0 > "$TEST_COUNTER"
wait_for_marker() {
    for _ in {1..200}; do [[ ! -f $1 ]] || return 0; sleep 0.02; done
    echo "Chybí značka: $1" >&2
    return 1
}
for first_exit in 0 42; do
    rm -rf "$TEST_BUILD_MARKERS"
    mkdir "$TEST_BUILD_MARKERS"
    : > "$TEST_BUILD_EVENTS"
    TEST_RUN_ID=first TEST_BUILD_EXIT=$first_exit bash scripts/build/images.sh dev > "$temporary/first.log" 2>&1 &
    first_pid=$!
    wait_for_marker "$TEST_BUILD_MARKERS/started-first"
    # Během simulovaného BuildKitu je zámek skutečně držený, nikoli jen při přípravě.
    if flock -n "$first/build.lock" true; then
        echo 'Import/export běží bez zámku' >&2; exit 1
    fi
    (
        cd "$temporary/second"
        touch "$TEST_BUILD_MARKERS/second-requested"
        TEST_RUN_ID=second TEST_BUILD_EXIT=0 bash scripts/build/images.sh dev
    ) > "$temporary/second.log" 2>&1 &
    second_pid=$!
    wait_for_marker "$TEST_BUILD_MARKERS/second-requested"
    sleep 0.2
    kill -0 "$second_pid"
    [[ ! -e $TEST_BUILD_MARKERS/started-second ]]
    touch "$TEST_BUILD_MARKERS/release"
    first_status=0
    wait "$first_pid" || first_status=$?
    [[ $first_status == "$first_exit" ]]
    wait "$second_pid"
    printf 'start first\nend first\nstart second\nend second\n' > "$temporary/expected-events"
    cmp "$temporary/expected-events" "$TEST_BUILD_EVENTS"
    [[ $(cat "$first/dev/index.json") == 'complete second' ]]
    flock -n "$first/build.lock" true
done
echo 'OK: sdílená cesta, převzetí, hlídač a souběžné importy/exporty ze dvou worktree; uvolnění zámku po úspěchu i chybě.'
