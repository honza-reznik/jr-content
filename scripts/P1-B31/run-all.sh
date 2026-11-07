#!/bin/sh
# Celý průchod po částech (každá část se vejde do limitu 590 s run-browser.sh). Výstup do ops/test-output/P1-B31/.
set -u
cd "$(dirname "$0")/../.."
for part in "--prepare --width=390 --role=admin" "--width=390 --role=client" "--width=360 --role=admin" "--width=360 --role=client" "--width=1440 --role=admin" "--width=1440 --role=client"; do
  tag=$(echo "$part" | sed 's/--prepare//; s/--//g; s/=//g; s/ //g')
  docker exec jrc-p1b31-jrc-redis-1 sh -c 'redis-cli --scan --pattern "rl:*" | xargs -r redis-cli del' >/dev/null 2>&1
  # shellcheck disable=SC2086
  JRC_PROJECT=jrc-p1b31 sh scripts/P1-B31/run-browser.sh $part > "ops/test-output/P1-B31/after-$tag.log" 2>&1
  echo "$part exit=$? pass=$(grep -c '"pass":true' ops/test-output/P1-B31/after-$tag.log) fail=$(grep -c '"pass":false' ops/test-output/P1-B31/after-$tag.log)"
done
