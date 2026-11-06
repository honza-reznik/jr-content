#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if ! bash scripts/build/download-guard.sh --check-parent 2>/dev/null; then
    exec bash scripts/build/download-guard.sh bash "$PWD/scripts/build/images.sh" "$@"
fi
export BUILDX_CONFIG=${BUILDX_CONFIG:-$PWD/.cache/docker-buildx}
project=${JRC_PROJECT:?Spusť přes make s JRC_PROJECT}
[[ $project =~ ^jrc(-[a-z0-9-]+)?$ ]] || exit 2
source scripts/build/cache.sh
prepare_build_cache
# Stejný zámek jako při převzetí cache, společný všem worktree. Deskriptor
# zůstává otevřený až do konce této dávky (i při chybě se zavře s procesem).
# Zamykáme před čtením index.json a odemykáme až po dokončení všech exportů.
exec {build_cache_lock}>"$cache_root/build.lock"
flock "$build_cache_lock"
for image in "$@"; do
    args=()
    case $image in
        dev) file=docker/Dockerfile; context=docker ;;
        bb)
            file=brightbean/Dockerfile; context=brightbean
            version=$(git rev-parse --short HEAD)
            git archive --format=tar.gz --prefix="jr-content-$version/" --output=brightbean/.jrc-source.tar.gz HEAD
            args+=(--build-arg "JRC_SOURCE_VERSION=$version") ;;
        ff-api) file=docker/Dockerfile.ff-api; context=. ;;
        ff-web)
            file=docker/Dockerfile.ff-web; context=.
            base_path=${JRC_BASE_PATH-$(sed -n 's/^JRC_BASE_PATH=//p' .env)}
            args+=(--build-arg "JRC_BASE_PATH=$base_path") ;;
        ingress) file=docker/Dockerfile.ingress; context=docker ;;
        s3) file=docker/Dockerfile.s3; context=docker ;;
        smtp-relay) file=docker/smtp-relay/Dockerfile; context=docker/smtp-relay ;;
        *) echo "Neznámý obraz: $image" >&2; exit 2 ;;
    esac
    cache="$cache_root/$image"
    if [[ -f $cache/index.json ]]; then args+=(--cache-from "type=local,src=$cache"); fi
    # Export do stejného OCI úložiště: obsah je adresovaný hashem, existující
    # bloky se nepřenášejí znovu. Nikdy nemažeme cache ani obrazy Dockeru.
    # Čas v automatickém attestation mění index i při stejných vrstvách. Dev je
    # základ dalších obrazů: stabilní manifest zabrání nové instalaci jejich balíčků.
    JRC_BUILD_LABEL="jrc-$image:$project" bash scripts/build/download-guard.sh docker buildx build --load --provenance=false --progress plain \
        --cache-to "type=local,dest=$cache,mode=max" \
        --build-arg "JRC_PROJECT=$project" "${args[@]}" \
        --tag "jrc-$image:$project" --file "$file" "$context"
done
