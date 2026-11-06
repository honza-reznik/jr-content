#!/usr/bin/env bash
set -euo pipefail

build_cache_dir() {
    local common
    common=$(git rev-parse --path-format=absolute --git-common-dir)
    realpath -m "${JRC_BUILD_CACHE_DIR:-$(dirname "$common")/.cache/buildkit}"
}

prepare_build_cache() {
    cache_root=$(build_cache_dir)
    mkdir -p "$cache_root"
    # Příprava vlastní jen svůj zámek; sestavovací dávka si jej drží výslovně
    # v images.sh po celou dobu importu i exportu BuildKitu.
    (
        # Společný zámek pro hlavní checkout i všechny propojené worktree.
        exec {cache_lock}>"$cache_root/build.lock"
        flock "$cache_lock"
        local legacy worktree image staging
        # Nejprve aktuální kopie, potom ostatní worktree: i první spuštění z main
        # najde již naplněný export bloku. Původní data se nemažou ani neupravují.
        while IFS= read -r -d '' worktree; do
            [[ $worktree == 'worktree '* ]] || continue
            legacy="${worktree#worktree }/.cache/buildkit"
            [[ $(realpath -m "$legacy") != "$cache_root" && -d $legacy ]] || continue
            # Starý spouštěč vytvářel tento zámek; otevření pro čtení nic nemění.
            if [[ -f $legacy/build.lock ]]; then
                exec {legacy_lock}<"$legacy/build.lock"
                flock "$legacy_lock"
            else
                legacy_lock=
            fi
            for image in dev bb ff-api ff-web ingress s3 smtp-relay; do
                if [[ -f $legacy/$image/index.json && ! -e $cache_root/$image ]]; then
                    staging=$(mktemp -d "$cache_root/.import-$image-XXXXXXXX")
                    cp -a --reflink=auto "$legacy/$image/." "$staging/"
                    mv "$staging" "$cache_root/$image"
                    printf 'Převzata místní cache %s → %s\n' "$legacy/$image" "$cache_root/$image"
                fi
            done
            if [[ -n $legacy_lock ]]; then
                flock -u "$legacy_lock"
                exec {legacy_lock}>&-
            fi
        done < <(printf 'worktree %s\0' "$PWD"; git worktree list --porcelain -z)
    )
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    case ${1:-} in
        '') build_cache_dir ;;
        --prepare) prepare_build_cache ;;
        *) echo 'Použití: cache.sh [--prepare]' >&2; exit 2 ;;
    esac
fi
