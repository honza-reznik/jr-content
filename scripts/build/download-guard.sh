#!/usr/bin/env bash
# Měří celé rozhraní hostitele, tedy konzervativně i souběžný cizí provoz.
set -euo pipefail
# Kanonická cesta umožní ověřit skutečný proces hlídače v /proc, nikoli
# důvěřovat příznaku předanému volajícím v prostředí.
guard_path=$(realpath "${BASH_SOURCE[0]}")
if [[ $0 != "$guard_path" ]]; then exec bash "$guard_path" "$@"; fi
parent_guard_active() {
    local ancestor=$PPID key value executable script mode
    while [[ $ancestor != 0 ]]; do
        [[ -r /proc/$ancestor/status ]] || return 1
        executable= script= mode=
        if {
            IFS= read -r -d '' executable &&
            IFS= read -r -d '' script &&
            IFS= read -r -d '' mode
        } < "/proc/$ancestor/cmdline" 2>/dev/null; then
            if [[ ${executable##*/} == bash && $script == "$guard_path" && $mode != --check-parent ]]; then
                return 0
            fi
        fi
        value=0
        while read -r key value _; do
            [[ $key != PPid: ]] || break
        done < "/proc/$ancestor/status"
        ancestor=$value
    done
    return 1
}
if [[ ${1:-} == --check-parent ]]; then
    parent_guard_active || { echo 'Interní sestavení vyžaduje skutečného nadřazeného hlídače.' >&2; exit 2; }
    exit 0
fi
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
if parent_guard_active; then
    # Nadřazený hlídač měří součet celé dávky. Zachovat jeho skupinu procesů.
    "$@" || result=$?
    finish
    exit "$result"
fi
export JRC_BUILD_INTERFACE=$iface
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
