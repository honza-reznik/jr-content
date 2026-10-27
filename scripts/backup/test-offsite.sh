#!/bin/sh
# P1-B04: samostatný průchod v čerstvém klonu HEAD a vlastním Docker projektu.
set -eu
: "${JRC_PROJECT:?JRC_PROJECT chybí}"
source_root=$(pwd -P)
suffix=$$
project="jrc-p1b04-offsite-$suffix"
test_dir="$source_root/ops/test-output/P1-B04-offsite-$suffix"
test_root="$test_dir/worktree"
artifact_dir="$test_root/ops/test-output/P1-B04-offsite-$suffix"
key="$artifact_dir/age-key.txt"
repo="$artifact_dir/repo"
remote="$artifact_dir/remote.git"
local_backups="$test_root/backups/$project"
app_port=$((24000 + suffix % 1000 * 4))
pg_port=$((app_port + 1))
s3_port=$((app_port + 2))
bb_port=$((app_port + 3))
[ ! -e "$test_dir" ] && [ ! -L "$test_dir" ] || { echo 'Testovací adresář již existuje.' >&2; exit 2; }
mkdir -p "$test_dir"

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -f "$key"
    if [ -d "$test_root" ]; then
        JRC_PROJECT="$project" make --no-print-directory -C "$test_root" down >/dev/null 2>&1 || true
        JRC_PROJECT="$project" docker compose --project-name "$project" --file "$test_root/docker-compose.yml" \
            --env-file "$test_root/.env" down --volumes >/dev/null 2>&1 || true
    fi
    if [ "$status" -eq 0 ]; then
        rm -rf "$test_dir" "$local_backups"
    else
        echo 'Test selhal; ponechávám pracovní data projektu k prohlédnutí, soukromý klíč byl smazán.' >&2
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

# Klonuj commit HEAD a přejdi do detached HEAD; žádný název větve není součástí testu.
git clone --quiet --no-hardlinks "$source_root" "$test_root"
git -C "$test_root" checkout --quiet --detach HEAD
# Přenes pracovní diff do klonu, ale aplikace a obrazy zůstávají na původním HEAD.
git diff --binary HEAD | git -C "$test_root" apply
mkdir -p "$artifact_dir"
export JRC_PROJECT="$project" JRC_APP_PORT="$app_port" JRC_POSTGRES_PORT="$pg_port" JRC_S3_PORT="$s3_port" JRC_BB_PORT="$bb_port"
BUILDX_CONFIG="$source_root/.cache/docker-buildx" JRC_MODE=dev make --no-print-directory -C "$test_root" env >/dev/null
JRC_PROJECT="$project" make --no-print-directory -C "$test_root" check-project-ports >/dev/null
version=$(git -C "$test_root" rev-parse --short HEAD)
git -C "$test_root" archive --format=tar.gz --prefix="jr-content-$version/" \
    --output="$test_root/brightbean/.jrc-source.tar.gz" HEAD
JRC_PROJECT="$project" docker compose --project-name "$project" --file "$test_root/docker-compose.yml" \
    --env-file "$test_root/.env" up --detach --build --wait --wait-timeout 120 >/dev/null

docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
    --volume "$test_root:/workspace" --workdir /workspace "jrc-dev:$project" \
    sh -c 'age-keygen -o /workspace/ops/test-output/P1-B04-offsite-'"$suffix"'/age-key.txt >/dev/null 2>&1 && age-keygen -y /workspace/ops/test-output/P1-B04-offsite-'"$suffix"'/age-key.txt' > "$test_dir/recipient"
recipient=$(cat "$test_dir/recipient")
docker run --rm --pull never --network none --user "$(id -u):$(id -g)" \
    --volume "$test_root:/workspace" --workdir /workspace "jrc-dev:$project" \
    git init --bare --quiet --initial-branch=main "/workspace/ops/test-output/P1-B04-offsite-$suffix/remote.git"
python3 - "$test_root/.env" "$recipient" "$repo" "$remote" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1]); text=p.read_text()
values={'JRC_BACKUP_AGE_RECIPIENT':sys.argv[2], 'JRC_BACKUP_GIT_DIR':sys.argv[3],
        'JRC_BACKUP_GIT_REMOTE':sys.argv[4], 'JRC_BACKUP_KEEP_OFFSITE':'2'}
for key,value in values.items():
    line=re.compile(rf'^{re.escape(key)}=.*$', re.M)
    text=line.sub(f'{key}={value}', text) if line.search(text) else text+f'\n{key}={value}\n'
p.write_text(text); p.chmod(0o600)
PY

before="$test_dir/inventory-before.json"
after="$test_dir/inventory-after.json"
for index in 1 2 3; do
    JRC_PROJECT="$project" make --no-print-directory -C "$test_root" backup >/dev/null
    stamp=$(find "$local_backups" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*' -printf '%f\n' | sort | tail -n 1)
    [ -n "$stamp" ] || { echo 'Místní záloha nevznikla.' >&2; exit 1; }
    # Rozpoznatelný marker je jen v manifestu uvnitř šifrovaného payloadu.
    python3 - "$local_backups/$stamp/manifest.json" "$index" <<'PY'
from pathlib import Path
import json,sys
p=Path(sys.argv[1]); data=json.loads(p.read_text())
data['p1b04_test_marker']=f'P1B04-TEST-SECRET-{sys.argv[2]}@example.invalid'
p.write_text(json.dumps(data, ensure_ascii=False, indent=1))
PY
    JRC_PROJECT="$project" make --no-print-directory -C "$test_root" backup-verify BACKUP="backups/$project/$stamp" >/dev/null
    (cd "$test_root" && JRC_PROJECT="$project" sh scripts/backup/offsite.sh backup >/dev/null)
    # Od prvního commitu nastav origin/pushurl na omyl; hostitelský přímý push URL musí přesto fungovat.
    if [ "$index" -eq 1 ]; then
        git -C "$repo" remote add origin "$test_dir/wrong-origin.git"
        git -C "$repo" config remote.origin.pushurl "$test_dir/wrong-pushurl.git"
    fi
    # Simulace hostitele: explicitní adresa z JRC_BACKUP_GIT_REMOTE, nikoli origin.
    git -C "$repo" push --quiet "$remote" zalohy
    if [ "$index" -eq 1 ]; then
        # Obnov hostitelský klon ze vzdálené větve a nastav chybné origin/pushurl.
        rm -rf "$repo"
        git clone --quiet --branch zalohy "$remote" "$repo"
        git -C "$repo" remote set-url origin "$test_dir/wrong-origin.git"
        git -C "$repo" config remote.origin.pushurl "$test_dir/wrong-pushurl.git"
    fi
    [ "$index" -eq 3 ] || sleep 1
done

age_count=$(find "$repo" -maxdepth 1 -type f -name '*.age' | wc -l)
[ "$age_count" -eq 2 ] || { echo "Rotace ponechala $age_count archivů místo dvou." >&2; exit 1; }
commit_count=$(git --git-dir="$remote" rev-list --count refs/heads/zalohy)
[ "$commit_count" -eq 3 ] || { echo "Vzdálená historie má $commit_count commitů místo tří." >&2; exit 1; }
python3 - "$repo" "$remote" <<'PY'
from pathlib import Path
import subprocess,sys
repo,remote=map(Path,sys.argv[1:])
for path in repo.glob('*.age'):
    if path.is_symlink() or not path.is_file(): raise SystemExit('Neplatný soubor v pracovním stromu.')
    if any(x in path.read_bytes() for x in (b'P1B04-TEST-SECRET', b'brightbean', b'freeframe', b'CREATE TABLE', b'@example.invalid')):
        raise SystemExit('Testovací data unikla do šifrovaného souboru.')
objects=subprocess.check_output(['git','--git-dir='+str(remote),'rev-list','--objects','--all'],text=True)
for line in objects.splitlines():
    oid=line.split()[0]
    blob=subprocess.check_output(['git','--git-dir='+str(remote),'cat-file','-p',oid])
    if any(x in blob for x in (b'P1B04-TEST-SECRET', b'brightbean', b'freeframe', b'CREATE TABLE', b'@example.invalid')):
        raise SystemExit('Testovací data unikla do historie vzdáleného repozitáře.')
print('OK: všechny aktuální soubory i commity v historii obsahují pouze neprůhledné šifrované archivy.')
PY
stamp=$(find "$repo" -maxdepth 1 -type f -name '*.age' -printf '%f\n' | sed 's/\.age$//' | sort | tail -n 1)
if docker run --rm --pull never --network none --volume "$test_root:/workspace" --workdir /workspace "jrc-dev:$project" \
    age --decrypt --output /dev/null "/workspace/ops/test-output/P1-B04-offsite-$suffix/repo/$stamp.age" >/dev/null 2>&1; then
    echo 'Dešifrování bez soukromého klíče nemělo projít.' >&2; exit 1
fi

# Ověř, že noční logy při chybě zachovají jen povolenou zprávu a číselný kód.
mkdir -p "$test_dir/fake-bin" "$test_root/ops/logs"
cat > "$test_dir/fake-bin/make" <<'SH'
#!/bin/sh
echo 'P1B04-TEST-SECRET-FAKE-OUTPUT' >&2
exit "${FAKE_MAKE_STATUS:-0}"
SH
cat > "$test_dir/fake-bin/git" <<'SH'
#!/bin/sh
case " $* " in
    *' push '*) echo 'P1B04-TEST-SECRET-FAKE-GIT-OUTPUT' >&2; exit "${FAKE_GIT_STATUS:-0}" ;;
    *) exec /usr/bin/git "$@" ;;
esac
SH
chmod +x "$test_dir/fake-bin/make" "$test_dir/fake-bin/git"
cp "$test_root/ops/backup-nightly.sh" "$test_dir/nightly.sh"
PATH="$test_dir/fake-bin:/usr/bin:/bin" JRC_BACKUP_NIGHTLY_ROOT="$test_root" FAKE_MAKE_STATUS=23 sh "$test_dir/nightly.sh" || result=$?
[ "${result:-0}" -eq 23 ] || { echo 'Noční skript nevrátil kód chyby zálohy.' >&2; exit 1; }
log=$(find "$test_root/ops/logs" -name 'backup-nightly-*.log' -type f | head -n 1)
grep -qx 'CHYBA: příprava zálohy selhala (kód 23).' "$log"
! grep -q 'P1B04-TEST-SECRET' "$log"
rm -f "$log"
PATH="$test_dir/fake-bin:/usr/bin:/bin" JRC_BACKUP_NIGHTLY_ROOT="$test_root" FAKE_MAKE_STATUS=0 FAKE_GIT_STATUS=29 sh "$test_dir/nightly.sh" || result=$?
[ "${result:-0}" -eq 29 ] || { echo 'Noční skript nevrátil kód chyby push.' >&2; exit 1; }
grep -qx 'CHYBA: push selhal (kód 29).' "$log"
! grep -q 'P1B04-TEST-SECRET' "$log"

# Round-trip do prázdného projektu a porovnání inventáře obou databází.
JRC_PROJECT="$project" docker compose --project-name "$project" --file "$test_root/docker-compose.yml" --env-file "$test_root/.env" \
    exec -T jrc-dev sh -c 'cd /workspace && python3 scripts/backup/jrc_backup.py inventory' > "$before"
JRC_PROJECT="$project" make --no-print-directory -C "$test_root" down >/dev/null
JRC_PROJECT="$project" docker compose --project-name "$project" --file "$test_root/docker-compose.yml" --env-file "$test_root/.env" down --volumes >/dev/null
rm -rf "$local_backups"
JRC_PROJECT="$project" make --no-print-directory -C "$test_root" restore-offsite BACKUP="$stamp" AGE_KEY_FILE="$key" >/dev/null
JRC_PROJECT="$project" make --no-print-directory -C "$test_root" backup-verify BACKUP="backups/$project/$stamp" >/dev/null
 (cd "$test_root" && JRC_PROJECT="$project" sh scripts/backup/backup.sh restore-guard "backups/$project/$stamp" >/dev/null)
 (cd "$test_root" && JRC_PROJECT="$project" sh scripts/backup/backup.sh restore-data "backups/$project/$stamp" >/dev/null)
JRC_PROJECT="$project" JRC_MODE=dev make --no-print-directory -C "$test_root" test-up >/dev/null
 (cd "$test_root" && JRC_PROJECT="$project" sh scripts/backup/backup.sh restore-finish >/dev/null)
JRC_PROJECT="$project" docker compose --project-name "$project" --file "$test_root/docker-compose.yml" --env-file "$test_root/.env" \
    exec -T jrc-dev sh -c 'cd /workspace && python3 scripts/backup/jrc_backup.py inventory' > "$after"
python3 - "$before" "$after" <<'PY'
import json,sys
before=json.load(open(sys.argv[1])); after=json.load(open(sys.argv[2]))
volatile={'public.background_task','public.background_task_completedtask','public.django_session'}
stable=lambda inventory: {db:{table:count for table,count in tables.items() if table not in volatile} for db,tables in inventory['tables'].items()}
if stable(before) != stable(after): raise SystemExit('Po obnově se počty řádků trvalých tabulek liší.')
print('OK: obě databáze po round-trip obnově odpovídají původnímu inventáři.')
PY
echo 'OK: rotace, historie, cílový push, neprůhlednost, chybové logy a round-trip prošly.'
