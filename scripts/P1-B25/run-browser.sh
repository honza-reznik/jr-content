#!/bin/sh
set -eu
case "${1:-}" in before|after|review|org) ;; *) exit 2;; esac
# No other project is accepted. Browser and Node share a 3 GiB cgroup.
[ "${JRC_PROJECT:-}" = jrc-p1b25 ] || exit 2
browser_script=browser.cjs
[ "$1" != review ] || browser_script=browser-review.cjs
[ "$1" != org ] || browser_script=browser-org.cjs
timeout 600s docker run --rm --name jrc-p1b25-browser --pull never --network host \
  --memory 3g --memory-swap 3g --cpus 2 --pids-limit 256 \
  --user "$(id -u):$(id -g)" --volume "$(pwd):/workspace" \
  jrc-dev:jrc-p1b25 timeout -k 10s 540s node --max-old-space-size=2048 "/workspace/scripts/P1-B25/$browser_script" "$1"
