#!/bin/sh
set -eu

root=$(git rev-parse --show-toplevel)
if [ -n "$(git -C "$root" status --porcelain --untracked-files=all)" ]; then
    echo "Produkční sestavení vyžaduje čistý worktree; archiv i obrazy se sestavují z HEAD." >&2
    exit 1
fi
