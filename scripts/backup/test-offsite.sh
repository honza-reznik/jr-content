#!/bin/sh
# P1-B04: end-to-end šifrování a databázová obnova výhradně v jrc-p1b04.
set -eu
project=${JRC_PROJECT:?JRC_PROJECT chybí}
[ "$project" = jrc-p1b04 ] || { echo 'Test offsite smí běžet jen jako jrc-p1b04.' >&2; exit 2; }
root=$(pwd -P)
test_dir=ops/test-output/P1-B04
key="$root/$test_dir/age-key.txt"
repo="$root/$test_dir/repo"
remote="$root/$test_dir/remote.git"
local_backups="$root/backups/$project"
if [ -e "$test_dir" ] || [ -L "$test_dir" ] || [ -e "$local_backups" ] || [ -L "$local_backups" ]; then
    echo 'Testovací výstup nebo lokální zálohy už existují; ponechávám je beze změny.' >&2
    exit 2
fi
mkdir -p "$test_dir"
[ ! -L "$repo" ] || { echo 'Testovací cesta nesmí obsahovat odkazy.' >&2; exit 2; }
cleanup() {
    status=$?
    trap - EXIT
    rm -f "$key"
    # .env původní obsah je nejprve zkopírován z pomocné kopie níže.
    if [ -f "$test_dir/env.saved" ]; then cp "$test_dir/env.saved" .env; chmod 600 .env; fi
    rm -f "$test_dir/env.saved"
    if [ "$status" -eq 0 ]; then
        JRC_PROJECT="$project" make down >/dev/null 2>&1 || status=1
        if [ "$status" -eq 0 ]; then
            JRC_PROJECT="$project" docker compose --project-name "$project" --file docker-compose.yml --env-file .env down --volumes >/dev/null 2>&1 || status=1
        fi
        rm -rf "$repo" "$local_backups"
        rmdir "$(dirname "$local_backups")" 2>/dev/null || true
        rm -rf "$test_dir"
    else
        echo "Test selhal; projekt a výstupy ponechány pro kontrolu (soukromý age klíč smazán)." >&2
    fi
    exit "$status"
}
trap cleanup EXIT
cp .env "$test_dir/env.saved"

docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
    --volume "$root:/workspace" --workdir /workspace "jrc-dev:$project" \
    sh -c 'age-keygen -o /workspace/ops/test-output/P1-B04/age-key.txt >/dev/null 2>&1 && age-keygen -y /workspace/ops/test-output/P1-B04/age-key.txt' > "$test_dir/recipient"
recipient=$(cat "$test_dir/recipient")
docker run --rm --pull never --network none --user "$(id -u):$(id -g)" --volume "$root:/workspace" --workdir /workspace "jrc-dev:$project" \
    git init --bare --initial-branch=main /workspace/ops/test-output/P1-B04/remote.git >/dev/null
python3 - "$recipient" "$repo" "$remote" <<'PY'
from pathlib import Path
import re, sys
p=Path('.env'); text=p.read_text()
for key,value in {'JRC_BACKUP_AGE_RECIPIENT':sys.argv[1], 'JRC_BACKUP_GIT_DIR':sys.argv[2], 'JRC_BACKUP_GIT_REMOTE':sys.argv[3], 'JRC_BACKUP_KEEP_OFFSITE':'2'}.items():
    line=re.compile(rf'^{re.escape(key)}=.*$', re.M)
    text=line.sub(f'{key}={value}', text) if line.search(text) else text+f'\n{key}={value}\n'
p.write_text(text); p.chmod(0o600)
PY

before="$test_dir/inventory-before.json"
after="$test_dir/inventory-after.json"
restore_clone="$test_dir/restore-worktree"
JRC_PROJECT="$project" docker compose --project-name "$project" --file docker-compose.yml --env-file .env \
    exec -T jrc-dev sh -c 'cd /workspace && python3 scripts/backup/jrc_backup.py inventory' > "$before"
JRC_PROJECT="$project" make backup-offsite
stamp=$(find "$repo" -maxdepth 1 -type f -name '*.age' -printf '%f\n' | sed 's/\.age$//' | sort | tail -n 1)
[ -n "$stamp" ] || { echo 'Chybí šifrovaný archiv.' >&2; exit 1; }
refs=$(docker run --rm --pull never --network none --volume "$root:/workspace" --workdir /workspace "jrc-dev:$project" \
    git --git-dir=/workspace/ops/test-output/P1-B04/remote.git for-each-ref --format='%(refname)' refs/heads)
[ "$refs" = refs/heads/zalohy ] || { echo "V místním vzdáleném repozitáři je neočekávaná větev: $refs" >&2; exit 1; }
grep -aEq 'brightbean|freeframe|postgres|CREATE TABLE|@example\.com' "$repo/$stamp.age" && { echo 'V šifrovaném souboru je čitelný text.' >&2; exit 1; } || true
if docker run --rm --pull never --network none --volume "$root:/workspace" --workdir /workspace "jrc-dev:$project" \
    age --decrypt --output /dev/null "/workspace/ops/test-output/P1-B04/repo/$stamp.age" 2>/dev/null; then
    echo 'Dešifrování bez klíče nemělo projít.' >&2; exit 1
fi

# Zastav a smaž pouze svazky tohoto ověřovacího projektu. Obnova sama spustí minimální služby.
python3 - "$root/backups/$project/$stamp" "$root/backups/$project" "$stamp" <<'PY'
from pathlib import Path
import json, shutil, sys
target, parent = map(Path, sys.argv[1:3]); stamp=sys.argv[3]
if target.is_symlink():
    raise SystemExit('Odmítám odstranit symbolický odkaz.')
parent=parent.resolve(strict=True)
target=target.resolve(strict=True)
if target.parent != parent:
    raise SystemExit('Odmítám odstranit cestu mimo zálohy vlastního testu.')
manifest=json.loads((target/'manifest.json').read_text())
if manifest.get('stamp') != stamp:
    raise SystemExit('Místní záloha neodpovídá testovacímu razítku.')
shutil.rmtree(target)
PY
JRC_PROJECT="$project" make down >/dev/null
JRC_PROJECT="$project" docker compose --project-name "$project" --file docker-compose.yml --env-file .env down --volumes >/dev/null
JRC_PROJECT="$project" make restore-offsite BACKUP="$stamp" AGE_KEY_FILE="$key"
JRC_PROJECT="$project" make backup-verify BACKUP="backups/$project/$stamp"
git clone --quiet --no-hardlinks --branch blok/P1-B04 "$root" "$restore_clone"
chmod 600 .env
cp .env "$restore_clone/.env"
mkdir -p "$restore_clone/backups/$project"
cp -a "backups/$project/$stamp" "$restore_clone/backups/$project/"
JRC_PROJECT="$project" make -C "$restore_clone" restore BACKUP="backups/$project/$stamp"
JRC_PROJECT="$project" docker compose --project-name "$project" --file "$restore_clone/docker-compose.yml" --env-file "$restore_clone/.env" \
    exec -T jrc-dev sh -c 'cd /workspace && python3 scripts/backup/jrc_backup.py inventory' > "$after"
python3 - "$before" "$after" <<'PY'
import json,sys
before=json.load(open(sys.argv[1])); after=json.load(open(sys.argv[2]))
volatile={'public.background_task','public.background_task_completedtask','public.django_session'}
stable=lambda inventory: {db:{table:count for table,count in tables.items() if table not in volatile} for db,tables in inventory['tables'].items()}
if stable(before) != stable(after):
    raise SystemExit('Po offsite obnově se počty řádků trvalých tabulek liší.')
print('OK: počty řádků trvalých tabulek obou databází po make restore odpovídají zdroji.')
PY
echo 'OK: archiv je neprůhledný, bez klíče nejde otevřít a make restore obnovil obě databáze.'
JRC_PROJECT="$project" make --no-print-directory -C "$restore_clone" down >/dev/null
JRC_PROJECT="$project" docker compose --project-name "$project" --file "$restore_clone/docker-compose.yml" --env-file "$restore_clone/.env" down --volumes >/dev/null
python3 - "$restore_clone" <<'PY'
from pathlib import Path
import shutil, sys
p=Path(sys.argv[1])
if p.is_symlink() or p.name != 'restore-worktree': raise SystemExit('Nečekaný pomocný worktree.')
shutil.rmtree(p)
PY
