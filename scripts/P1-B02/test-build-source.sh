#!/bin/sh
set -eu

check_script=${1:?path to clean-worktree check is required}
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
git -C "$temporary" init -q
git -C "$temporary" config user.name Regression
git -C "$temporary" config user.email regression@example.invalid
cp "$check_script" "$temporary/check-clean-worktree.sh"
printf 'committed\n' >"$temporary/tracked.txt"
git -C "$temporary" add tracked.txt
git -C "$temporary" add check-clean-worktree.sh
git -C "$temporary" commit -qm initial
(cd "$temporary" && sh check-clean-worktree.sh)
printf 'uncommitted\n' >>"$temporary/tracked.txt"
if (cd "$temporary" && sh check-clean-worktree.sh >/dev/null 2>&1); then
    echo "dirty tracked worktree was accepted" >&2
    exit 1
fi
git -C "$temporary" checkout -- tracked.txt
touch "$temporary/untracked.txt"
if (cd "$temporary" && sh check-clean-worktree.sh >/dev/null 2>&1); then
    echo "untracked worktree was accepted" >&2
    exit 1
fi
echo "OK: čistý worktree projde, necommitnuté i nové soubory build zastaví."
