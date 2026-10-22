#!/bin/sh
# F1-B06: ověření postupu z ADR-004 (git subtree: převzetí kódu a později oprav z původního projektu).
# Běží jen v Dockeru, bez sítě, nad dočasnou složkou. Původní klon Brightbean z F1-B01 se jen čte.
# Klon z auditu je „mělký“ (jen jeden commit), proto si skript z jeho stromu vyrobí malý falešný
# „upstream“ se dvěma commity: A = auditovaná verze 48801fd, B = A + změna v souboru, který
# upravujeme i my. Ostrý postup v ADR-004 bere kód přímo z GitHubu.
#
# Spuštění (z kořene worktree F1-B06):
#   docker run --rm --name jrc-f1b06-overeni --network none --user "$(id -u):$(id -g)" -e HOME=/tmp/h \
#     -v /home/honza/jr-content-wt/F1-B01/audit/brightbean-studio:/src/bb:ro \
#     -v "$PWD/docs-arch/F1-B06:/tools:ro" jrc-dev:local sh /tools/overeni-subtree.sh
set -eu
BB_COMMIT=48801fdcd1009e6ed4863fa9942222a478a297e2
W=$(mktemp -d)
mkdir -p "$HOME"
git config --global --add safe.directory '*'
git config --global user.email overeni@jrc.test
git config --global user.name "F1-B06 overeni"
git config --global init.defaultBranch main

# Falešný upstream: A a B
mkdir "$W/up" && cd "$W/up" && git init -q
git -C /src/bb archive "$BB_COMMIT" | tar -x
git add -A && git commit -qm "upstream A (= $BB_COMMIT)"
A=$(git rev-parse HEAD)
echo "# upstream B: oprava" >> apps/composer/models.py
git commit -qam "upstream B"
B=$(git rev-parse HEAD)

# Náš repozitář
mkdir "$W/jrc" && cd "$W/jrc" && git init -q
echo "JR Content" > README.md && git add . && git commit -qm init
git subtree add --prefix=brightbean "$W/up" "$A" --squash -m "Brightbean: import $A" >/dev/null 2>&1
test -f brightbean/manage.py
echo "OK 1: subtree add (kód je v brightbean/, jeden commit místo celé historie)"

# Naše úprava v souboru, který upstream v B také mění (jiné místo)
sed -i '1s/^/# JRC: nase uprava\n/' brightbean/apps/composer/models.py
git commit -qam "F2-Bxx: naše úprava"

git subtree pull --prefix=brightbean "$W/up" "$B" --squash -m "Brightbean: převzetí $B" >/dev/null 2>&1
head -1 brightbean/apps/composer/models.py | grep -q "JRC: nase uprava"
tail -1 brightbean/apps/composer/models.py | grep -q "upstream B: oprava"
echo "OK 2: subtree pull spojil naši úpravu s opravou z upstreamu bez konfliktu"
git log --format='%s%n%b' | grep -q "git-subtree-split: $B"
echo "OK 3: v historii je zapsáno, ze které verze upstreamu kód je (git-subtree-split)"
git log --oneline --graph | head -8

# Převzetí jedné opravy (ne celé nové verze): format-patch z upstreamu + am do podsložky
cd "$W/up"
echo "# upstream C: jen jedna oprava" >> apps/approvals/models.py
git commit -qam "upstream C: oprava approvals"
C=$(git rev-parse HEAD)
echo "# upstream D: nechceme" >> apps/calendar/views.py
git commit -qam "upstream D"
cd "$W/jrc"
git fetch -q "$W/up" main
git format-patch -1 "$C" --stdout | git am -q -3 --directory=brightbean
tail -1 brightbean/apps/approvals/models.py | grep -q "upstream C"
if grep -q "upstream D" brightbean/apps/calendar/views.py; then echo "CHYBA: převzala se i nechtěná změna D"; exit 1; fi
echo "OK 4: jednotlivá oprava převzata (format-patch | am --directory=brightbean), zbytek ne"

# Totéž ve worktree bloku (tak pracují kodéři): git -C <worktree> subtree add
git worktree add -q -b blok/F2-B01 "$W/wt-F2-B01" HEAD~2
git -C "$W/wt-F2-B01" subtree add --prefix=freeframe-test "$W/up" "$A" --squash -m "F2-B01: import" >/dev/null 2>&1
test -f "$W/wt-F2-B01/freeframe-test/manage.py"
echo "OK 5: subtree add funguje i ve worktree bloku (git -C <worktree>)"
rm -rf "$W"
