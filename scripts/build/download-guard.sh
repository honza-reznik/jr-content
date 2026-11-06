#!/usr/bin/env bash
# Měří celé rozhraní hostitele, tedy konzervativně i souběžný cizí provoz.
set -euo pipefail
limit=${JRC_BUILD_DOWNLOAD_LIMIT_MB:-500}
if [[ ${JRC_BUILD_CACHE_FILL:-0} == 1 ]]; then
    limit=${JRC_BUILD_CACHE_FILL_LIMIT_MB:-2000}
fi
[[ $limit =~ ^[1-9][0-9]*$ ]] || { echo 'Limit musí být kladné celé číslo MB.' >&2; exit 2; }
iface=${JRC_BUILD_INTERFACE:-$(awk '$2 == "00000000" && $4 == "0003" {print $1; exit}' /proc/net/route)}
counter=/sys/class/net/$iface/statistics/rx_bytes
[[ -n $iface && -r $counter ]] || { echo 'Nelze určit síťové rozhraní; nastav JRC_BUILD_INTERFACE.' >&2; exit 2; }
mkdir -p ops/logs
log=ops/logs/build-download.log
start=$(cat "$counter")
label=${JRC_BUILD_LABEL:-${1:?Chybí příkaz sestavení}}
printf '%s START project=%s interface=%s rx_bytes=%s limit_MB=%s command=%s\n' \
    "$(date -Iseconds)" "${JRC_PROJECT:?}" "$iface" "$start" "$limit" "$label" | tee -a "$log"
result=0
finish() {
    local end bytes
    end=$(cat "$counter")
    bytes=$((end - start))
    printf '%s END project=%s interface=%s rx_bytes=%s delta_bytes=%s downloaded_MB=%s status=%s command=%s\n' \
        "$(date -Iseconds)" "$JRC_PROJECT" "$iface" "$end" "$bytes" \
        "$(awk -v b="$bytes" 'BEGIN {printf "%.3f", b/1000000}')" "$result" "$label" | tee -a "$log"
}
if [[ ${JRC_BUILD_GUARD_ACTIVE:-0} == 1 ]]; then
    # Nadřazený hlídač měří součet celé dávky. Zachovat jeho skupinu procesů.
    "$@" || result=$?
    finish
    exit "$result"
fi
export JRC_BUILD_GUARD_ACTIVE=1 JRC_BUILD_INTERFACE=$iface
setsid "$@" &
child=$!
stop_child() {
    kill -TERM -- "-$child" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$child" 2>/dev/null || true
}
trap 'result=130; stop_child' INT
trap 'result=143; stop_child' TERM HUP
(
    while kill -0 "$child" 2>/dev/null; do
        end=$(cat "$counter")
        if (( end < start || end - start > limit * 1000000 )); then
            printf '%s LIMIT project=%s interface=%s rx_bytes=%s delta_bytes=%s limit_MB=%s\n' \
                "$(date -Iseconds)" "$JRC_PROJECT" "$iface" "$end" "$((end-start))" "$limit" | tee -a "$log" >&2
            stop_child
            exit
        fi
        sleep 0.2
    done
) &
watcher=$!
wait "$child" || result=$?
end=$(cat "$counter")
if (( end < start || end - start > limit * 1000000 )); then
    result=125
    stop_child
fi
kill "$watcher" 2>/dev/null || true
wait "$watcher" 2>/dev/null || true
end=$(cat "$counter")
if (( end < start || end - start > limit * 1000000 )); then result=125; fi
finish
exit "$result"
