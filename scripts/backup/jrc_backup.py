#!/usr/bin/env python3
"""Zálohy a obnova JR Content (F2-B09). Běží jen uvnitř Dockeru (obraz jrc-dev).

Podpříkazy volá skript scripts/backup/backup.sh (make backup, backup-verify, restore).
Zásady: záloha jen čte; obnova odmítne projekt `jrc` i neprázdný cíl; rotace maže jen
adresáře se platným manifestem přímo v backups/<projekt>/.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FORMAT = "jrc-backup/1"
MANIFEST = "manifest.json"
MAIN_PROJECT = "jrc"
PROJECT_RE = re.compile(r"jrc(-[a-z0-9-]+)?")
RESTORE_PROJECT_RE = re.compile(r"jrc-[a-z0-9-]+")
STAMP_RE = re.compile(r"[0-9]{8}-[0-9]{6}(-[0-9]+)?")
DATABASES = ("brightbean", "freeframe")
# Hodnoty, které se při obnově do existujícího .env nemusí shodovat.
ENV_IGNORED = re.compile(r"^(JRC_UID|JRC_GID|JRC_[A-Z0-9_]*PORT|JRC_S3_[A-Z]+_QUOTA)$")
PORT_KEYS = ("JRC_APP_PORT", "JRC_POSTGRES_PORT", "JRC_S3_PORT", "JRC_BB_PORT")
CHUNK = 1024 * 1024
EXTRA_ARGS = ("ContentType", "ContentDisposition", "ContentEncoding", "ContentLanguage", "CacheControl")


class BackupError(Exception):
    """Chyba, po které se nesmí pokračovat; zpráva je určena Honzovi."""


# --------------------------------------------------------------------------- pojistky


def check_project(project):
    if not isinstance(project, str) or not PROJECT_RE.fullmatch(project):
        raise BackupError("Neplatný název projektu (jrc nebo jrc-<malá písmena, číslice, pomlčky>).")
    return project


def check_restore_project(project):
    """Obnova smí cílit jen na oddělený ověřovací projekt, nikdy na hlavní `jrc`."""
    if project == MAIN_PROJECT:
        raise BackupError(
            "Obnova do hlavního projektu jrc je zakázaná (pojistka proti přepsání hlavních dat). "
            "Použij prázdný ověřovací projekt, např. JRC_PROJECT=jrc-obnova."
        )
    if not isinstance(project, str) or not RESTORE_PROJECT_RE.fullmatch(project):
        raise BackupError("Cílový projekt obnovy musí mít tvar jrc-<malá písmena, číslice, pomlčky>.")
    return project


def check_keep(value):
    try:
        keep = int(str(value).strip())
    except ValueError:
        raise BackupError("JRC_BACKUP_KEEP musí být celé číslo alespoň 1.") from None
    if keep < 1 or str(keep) != str(value).strip():
        raise BackupError("JRC_BACKUP_KEEP musí být celé číslo alespoň 1.")
    return keep


def sha256_file(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _safe_relative(name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != name:
        raise BackupError(f"Manifest obsahuje nebezpečnou cestu: {name!r}")
    return path


def load_manifest(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise BackupError(f"{directory} není adresář zálohy.")
    manifest_path = directory / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupError(f"V {directory} chybí {MANIFEST}.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BackupError(f"Manifest {manifest_path} nelze přečíst: {error}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError("Manifest má neznámý formát.")
    if manifest.get("complete") is not True:
        raise BackupError("Záloha není dokončená (manifest nemá complete=true).")
    return manifest


def validate_backup(directory, *, hashes=True):
    """Ověří manifest a soubory zálohy; vrací manifest, jinak BackupError.

    hashes=False kontroluje jen existenci a velikost (rychlá varianta, např. pro rotaci).
    Objekty S3 se kontrolují zvlášť (verify_objects), protože jich může být mnoho.
    """
    directory = Path(directory)
    manifest = load_manifest(directory)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise BackupError("Manifest neobsahuje seznam souborů.")
    for name, info in files.items():
        relative = _safe_relative(name)
        target = directory.joinpath(relative)
        if target.is_symlink() or not target.is_file():
            raise BackupError(f"V záloze chybí soubor {name}.")
        if target.stat().st_size != info.get("size"):
            raise BackupError(f"Soubor {name} má jinou velikost, než uvádí manifest.")
        if hashes:
            digest, _ = sha256_file(target)
            if digest != info.get("sha256"):
                raise BackupError(f"Kontrolní součet souboru {name} nesedí (poškozená záloha).")
    return manifest


def is_valid_backup(directory):
    try:
        validate_backup(directory, hashes=False)
    except BackupError:
        return False
    return True


def read_index(directory, bucket):
    path = Path(directory) / "s3" / bucket / "index.json"
    return json.loads(path.read_text(encoding="utf-8"))


def verify_objects(directory, manifest, log=print):
    """Přepočítá součty všech uložených objektů S3 a porovná je s indexy."""
    directory = Path(directory)
    total = 0
    for bucket in manifest.get("buckets", {}):
        entries = read_index(directory, bucket)
        folder = directory / "s3" / bucket / "objects"
        expected = {entry["file"] for entry in entries}
        present = {item.name for item in folder.iterdir()} if folder.is_dir() else set()
        if present != expected:
            raise BackupError(f"Bucket {bucket}: soubory objektů nesedí s indexem.")
        for entry in entries:
            digest, size = sha256_file(folder / entry["file"])
            if digest != entry["sha256"] or size != entry["size"]:
                raise BackupError(f"Bucket {bucket}: objekt {entry['key']!r} je poškozený.")
            total += 1
        declared = manifest["buckets"][bucket]
        if declared.get("objects") != len(entries):
            raise BackupError(f"Bucket {bucket}: počet objektů nesedí s manifestem.")
        log(f"  bucket {bucket}: {len(entries)} objektů, součty sedí")
    return total


def rotate(project_root, keep, log=print):
    """Ponechá `keep` nejnovějších platných záloh; maže jen platné adresy přímo v projektu."""
    keep = check_keep(keep)
    root = Path(project_root)
    if root.is_symlink() or not root.is_dir():
        raise BackupError(f"{root} není adresář záloh projektu.")
    resolved = root.resolve()
    if resolved.parent.name != "backups" or not PROJECT_RE.fullmatch(resolved.name):
        raise BackupError("Rotace smí pracovat jen v backups/<projekt>/.")
    candidates = []
    for item in sorted(resolved.iterdir(), key=lambda p: p.name):
        if item.is_symlink() or not item.is_dir() or not STAMP_RE.fullmatch(item.name):
            continue
        if not is_valid_backup(item):
            continue
        try:
            manifest = load_manifest(item)
        except BackupError:
            continue
        if manifest.get("project") != resolved.name or manifest.get("stamp") != item.name:
            continue
        candidates.append(item)
    removed = []
    for item in candidates[: max(len(candidates) - keep, 0)]:
        if item.resolve().parent != resolved:
            continue
        shutil.rmtree(item)
        removed.append(item.name)
        log(f"Rotace: odstraněna stará záloha {item.name}")
    return removed


# --------------------------------------------------------------------------- prostředí


def load_env_file(path):
    values = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            match = re.match(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$", line.rstrip("\n"))
            if match and not line.lstrip().startswith("#"):
                values[match.group(1)] = match.group(2).strip()
    return values


def compare_env(current, saved):
    """Vrací názvy nastavení (nikdy hodnoty), které se liší; porty a UID se ignorují."""
    differing = []
    for key in sorted(set(current) | set(saved)):
        if ENV_IGNORED.match(key):
            continue
        if current.get(key, "") != saved.get(key, ""):
            differing.append(key)
    return differing


def install_env(saved_path, target, overrides):
    """Vytvoří .env ze zálohy (práva 600, porty z přepínačů); existující soubor nikdy nepřepíše."""
    target = Path(target)
    if target.exists():
        raise BackupError(".env už existuje; nepřepisuji ho.")
    lines = []
    seen = set()
    with open(saved_path, encoding="utf-8") as stream:
        for line in stream:
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\n"))
            if match and match.group(1) in overrides and overrides[match.group(1)]:
                lines.append(f"{match.group(1)}={overrides[match.group(1)]}\n")
                seen.add(match.group(1))
            else:
                lines.append(line if line.endswith("\n") else line + "\n")
    for key, value in overrides.items():
        if value and key not in seen:
            lines.append(f"{key}={value}\n")
    temporary = target.with_name(".jrc-env.restore.env.local")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.writelines(lines)
        os.rename(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    os.chmod(target, 0o600)


# --------------------------------------------------------------------------- S3 a PostgreSQL


def bucket_credentials(env):
    """Každý bucket čte klíč, který k němu má přístup (v Garage nemá žádný klíč přístup ke všem)."""
    local = env.get("S3_BUCKET", "jrc-local")
    pairs = {
        local: ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
        "jrc-video": ("JRC_FF_S3_ACCESS_KEY", "JRC_FF_S3_SECRET_KEY"),
        "jrc-app": ("JRC_BB_S3_ACCESS_KEY", "JRC_BB_S3_SECRET_KEY"),
    }
    result = {}
    for bucket, (key_name, secret_name) in pairs.items():
        key, secret = env.get(key_name), env.get(secret_name)
        if not key or not secret:
            raise BackupError(f"V .env chybí {key_name} nebo {secret_name}.")
        result[bucket] = (key, secret)
    return result


def merged_env(workspace):
    env = {}
    path = Path(workspace) / ".env"
    if path.is_file():
        env.update(load_env_file(path))
    env.update({k: v for k, v in os.environ.items() if v != ""})
    return env


def s3_client(env, bucket):
    import boto3
    from botocore.config import Config

    key, secret = bucket_credentials(env)[bucket]
    return boto3.client(
        "s3",
        endpoint_url=env.get("S3_ENDPOINT_URL", "http://jrc-s3:3900"),
        region_name="garage",
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
    )


def list_objects(client, bucket):
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        yield from page.get("Contents", [])


def bucket_is_empty(client, bucket):
    return client.list_objects_v2(Bucket=bucket, MaxKeys=1).get("KeyCount", 0) == 0


def psql(sql, database=None, env=None):
    command = ["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-c", sql]
    if database:
        command += ["--dbname", database]
    result = subprocess.run(command, capture_output=True, text=True, env=env or os.environ)
    if result.returncode != 0:
        raise BackupError(f"psql selhalo: {result.stderr.strip()}")
    return result.stdout.strip()


USER_OBJECTS_SQL = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
    "AND n.nspname NOT LIKE 'pg_temp%' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
)


def database_is_empty(database):
    return int(psql(USER_OBJECTS_SQL, database)) == 0


def ensure_database(database):
    exists = psql(f"SELECT 1 FROM pg_database WHERE datname = '{database}'")
    if not exists:
        psql(f'CREATE DATABASE "{database}"')


def pg_version():
    return psql("SHOW server_version")


def row_counts(database):
    sql = (
        "SELECT format('%I.%I', table_schema, table_name) || '=' || "
        "(xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), "
        "false, true, '')))[1]::text FROM information_schema.tables "
        "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY 1"
    )
    counts = {}
    for line in psql(sql, database).splitlines():
        name, _, number = line.rpartition("=")
        counts[name] = int(number)
    return counts


# --------------------------------------------------------------------------- podpříkazy


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def human(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def cmd_start(args):
    """Krok 1: kontroly, odhad místa a rozpracovaný adresář (výpisy databází dělá host, viz backup.sh)."""
    os.umask(0o077)
    project = check_project(args.project)
    keep = check_keep(args.keep)
    if not STAMP_RE.fullmatch(args.stamp):
        raise BackupError("Neplatné časové razítko zálohy.")
    workspace = Path(args.workspace)
    env = merged_env(workspace)
    credentials = bucket_credentials(env)
    unknown = sorted(set(args.buckets.split()) - set(credentials)) if args.buckets else []
    if unknown:
        raise BackupError(
            f"Garage obsahuje bucket(y), které záloha nezná: {', '.join(unknown)}. "
            "Nic by se nezálohovalo, proto zálohu zastavuji."
        )
    root = workspace / "backups"
    project_root = root / project
    for folder in (root, project_root):
        folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
    partial = project_root / f"{args.stamp}.partial"
    if partial.exists() or (project_root / args.stamp).exists():
        raise BackupError(f"Záloha {args.stamp} už existuje.")

    # Odhad místa: databáze + buckety (média ze svazku přidá host; rezerva 1 GiB).
    needed = sum(int(psql(f"SELECT pg_database_size('{db}')")) for db in DATABASES)
    clients = {bucket: s3_client(env, bucket) for bucket in credentials}
    for bucket, client in clients.items():
        needed += sum(item["Size"] for item in list_objects(client, bucket))
    free = shutil.disk_usage(project_root).free
    if free < needed * 1.1 + (1 << 30):
        raise BackupError(f"Málo místa na disku: volno {human(free)}, potřeba asi {human(int(needed * 1.1) + (1 << 30))}.")

    partial.mkdir(mode=0o700)
    (partial / "postgres").mkdir(mode=0o700)
    state = {
        "project": project,
        "stamp": args.stamp,
        "keep": keep,
        "postgres_version": pg_version(),
        "started_at": utc_now(),
    }
    (partial / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


def cmd_copy_objects(args):
    """Krok 2 (po výpisech databází): kopie všech bucketů Garage do zálohy."""
    os.umask(0o077)
    project = check_project(args.project)
    workspace = Path(args.workspace)
    env = merged_env(workspace)
    partial = workspace / "backups" / project / f"{args.stamp}.partial"
    state_path = partial / "state.json"
    if not state_path.is_file():
        raise BackupError("Rozpracovaná záloha neexistuje.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for database in DATABASES:
        dump = partial / "postgres" / f"{database}.dump"
        if not dump.is_file() or dump.stat().st_size == 0:
            raise BackupError(f"Chybí výpis databáze {database}; S3 se nekopíruje.")
    clients = {bucket: s3_client(env, bucket) for bucket in bucket_credentials(env)}
    print("Záloha: soubory v úložišti S3 (po databázi)")
    summary = {}
    vanished = {}
    for bucket, client in clients.items():
        folder = partial / "s3" / bucket
        (folder / "objects").mkdir(mode=0o700, parents=True)
        entries, missing = [], []
        for number, item in enumerate(list_objects(client, bucket), start=1):
            key = item["Key"]
            name = f"{number:08d}"
            try:
                response = client.get_object(Bucket=bucket, Key=key)
            except client.exceptions.NoSuchKey:
                missing.append(key)  # smazáno během zálohy; databáze je starší, viz omezení
                continue
            digest = hashlib.sha256()
            size = 0
            with open(folder / "objects" / name, "wb") as stream:
                for chunk in response["Body"].iter_chunks(CHUNK):
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size != response["ContentLength"]:
                raise BackupError(f"Objekt {bucket}/{key} se stáhl neúplně.")
            entry = {"key": key, "file": name, "size": size, "sha256": digest.hexdigest()}
            for field in EXTRA_ARGS:
                if response.get(field):
                    entry[field] = response[field]
            if response.get("Metadata"):
                entry["Metadata"] = response["Metadata"]
            entries.append(entry)
        (folder / "index.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
        summary[bucket] = {"objects": len(entries), "bytes": sum(e["size"] for e in entries)}
        if missing:
            vanished[bucket] = missing
        print(f"  {bucket}: {len(entries)} objektů, {human(summary[bucket]['bytes'])}")
    state.update({"buckets": summary, "vanished": vanished})
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")


def cmd_finish(args):
    """Krok 3: kontrolní součty, manifest, přejmenování na hotovou zálohu a rotace."""
    os.umask(0o077)
    project = check_project(args.project)
    keep = check_keep(args.keep)
    workspace = Path(args.workspace)
    project_root = workspace / "backups" / project
    partial = project_root / f"{args.stamp}.partial"
    if not partial.is_dir():
        raise BackupError("Rozpracovaná záloha neexistuje.")
    state = json.loads((partial / "state.json").read_text(encoding="utf-8"))
    if state["project"] != project or state["stamp"] != args.stamp:
        raise BackupError("Rozpracovaná záloha patří jinému projektu.")
    media = partial / "bb-media.tar"
    if args.media == "present" and not media.is_file():
        raise BackupError("Chybí archiv svazku jrc-bb-media.")
    if args.media == "absent" and media.exists():
        raise BackupError("Archiv médií existuje, i když svazek nebyl nalezen.")
    env_included = False
    if not args.no_env:
        source = workspace / ".env"
        if not source.is_file():
            raise BackupError("Chybí .env; bez něj by nešlo záložní data obnovit. (JRC_BACKUP_NO_ENV=1 jej vynechá.)")
        shutil.copyfile(source, partial / "env.backup")
        os.chmod(partial / "env.backup", 0o600)
        env_included = True
    files = {}
    # Zálohy obsahují tajemství i data: jen vlastník (adresáře 700, soubory 600).
    for path in [partial, *partial.rglob("*")]:
        if not path.is_symlink():
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
    for path in sorted(partial.rglob("*")):
        relative = path.relative_to(partial).as_posix()
        if not path.is_file() or relative in (MANIFEST, "state.json") or relative.startswith("s3/") and "/objects/" in relative:
            continue
        digest, size = sha256_file(path)
        files[relative] = {"size": size, "sha256": digest}
    manifest = {
        "format": FORMAT,
        "complete": True,
        "project": project,
        "stamp": args.stamp,
        "created_at": utc_now(),
        "code": {"git_rev": args.git_rev, "dirty": args.git_dirty == "yes"},
        "postgres": {
            "server_version": state["postgres_version"],
            "databases": {
                db: files[f"postgres/{db}.dump"]["size"] for db in DATABASES if f"postgres/{db}.dump" in files
            },
        },
        "buckets": state["buckets"],
        "vanished_during_backup": state["vanished"],
        "bb_media": {"included": args.media == "present", "bytes": files.get("bb-media.tar", {}).get("size", 0)},
        "env_included": env_included,
        "files": files,
        "notes": "Redis (použité vstupenky) se nezálohuje. Postup obnovy: aplikace/provoz/zalohy.md.",
    }
    temporary = partial / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.rename(temporary, partial / MANIFEST)
    (partial / "state.json").unlink()
    final = project_root / args.stamp
    os.rename(partial, final)
    # Poslední pojistka: hotová záloha musí projít vlastní kontrolou (bez objektů, ty mají součty z čtení).
    validate_backup(final)
    total_objects = sum(b["objects"] for b in manifest["buckets"].values())
    total_bytes = sum(f["size"] for f in files.values()) + sum(b["bytes"] for b in manifest["buckets"].values())
    print(f"Záloha hotová: {final.relative_to(workspace)} ({total_objects} objektů S3, celkem {human(total_bytes)})")
    if manifest["vanished_during_backup"]:
        print("POZOR: některé objekty zmizely během zálohy (smazány aplikací); viz manifest.", file=sys.stderr)
    rotate(project_root, keep)


def cmd_verify(args):
    manifest = validate_backup(args.backup)
    print(f"Manifest a soubory sedí ({len(manifest['files'])} souborů).")
    count = verify_objects(args.backup, manifest)
    print(f"OK: záloha {manifest['project']}/{manifest['stamp']} je neporušená ({count} objektů S3).")


def cmd_precheck(args):
    """Před spuštěním čehokoli: projekt, platnost zálohy, shoda tajemství v .env."""
    check_restore_project(args.project)
    manifest = validate_backup(args.backup)
    print(f"Záloha {manifest['project']}/{manifest['stamp']} je platná (soubory a součty sedí).")
    saved = Path(args.backup) / "env.backup"
    target = Path(args.workspace) / ".env"
    if not manifest.get("env_included") or not saved.is_file():
        if not target.is_file():
            raise BackupError(
                "Záloha neobsahuje .env a v pracovní složce také žádný není. Bez tajemství "
                "(šifrovací klíče, hesla) data nelze použít. Dodej původní .env (chmod 600)."
            )
        print("POZOR: záloha neobsahuje .env; shodu tajemství nelze ověřit.", file=sys.stderr)
        return
    if target.is_file():
        differing = compare_env(load_env_file(target), load_env_file(saved))
        if differing:
            raise BackupError(
                "Stávající .env se liší od zálohy v nastavení: " + ", ".join(differing) + ". "
                "Obnova s jinými tajemstvími by data znehodnotila. Použij prázdnou pracovní složku "
                "bez .env (převezme se ze zálohy) nebo dodej původní .env."
            )
        print(".env odpovídá záloze (tajemství se shodují).")
    else:
        overrides = {key: os.environ.get(key, "") for key in PORT_KEYS}
        install_env(saved, target, overrides)
        print(".env vytvořen ze zálohy (práva 600).")


def _bucket_sets(manifest):
    return list(manifest.get("buckets", {}))


def cmd_restore_check(args):
    """Před vložením dat: databáze existují a jsou prázdné, buckety jsou prázdné."""
    project = check_restore_project(args.project)
    manifest = validate_backup(args.backup)
    env = merged_env(Path(args.workspace))
    clients = {bucket: s3_client(env, bucket) for bucket in _bucket_sets(manifest)}
    # Druhá pojistka: ani po prázdných svazcích nic nevkládáme do databáze nebo bucketu s obsahem.
    for database in DATABASES:
        ensure_database(database)
        if not database_is_empty(database):
            raise BackupError(f"Databáze {database} v projektu {project} není prázdná; obnovu zastavuji.")
    for bucket, client in clients.items():
        if not bucket_is_empty(client, bucket):
            raise BackupError(f"Bucket {bucket} v projektu {project} není prázdný; obnovu zastavuji.")
    print(f"Cíl {project}: databáze a buckety jsou prázdné.")


def cmd_restore_objects(args):
    """Po obnově databází: vloží soubory S3 do prázdných bucketů a ověří je."""
    project = check_restore_project(args.project)
    manifest = validate_backup(args.backup)
    env = merged_env(Path(args.workspace))
    clients = {bucket: s3_client(env, bucket) for bucket in _bucket_sets(manifest)}
    for bucket, client in clients.items():
        if not bucket_is_empty(client, bucket):
            raise BackupError(f"Bucket {bucket} v projektu {project} není prázdný; obnovu zastavuji.")
    backup = Path(args.backup)
    for bucket, client in clients.items():
        entries = read_index(backup, bucket)
        print(f"Obnova bucketu {bucket}: {len(entries)} objektů")
        for entry in entries:
            extra = {name: entry[name] for name in EXTRA_ARGS if name in entry}
            if "Metadata" in entry:
                extra["Metadata"] = entry["Metadata"]
            client.upload_file(
                str(backup / "s3" / bucket / "objects" / entry["file"]), bucket, entry["key"], ExtraArgs=extra
            )
    verify_restored_objects(backup, manifest, clients)
    print("OK: soubory S3 obnoveny a ověřeny.")


def verify_restored_objects(backup, manifest, clients):
    """Stáhne každý obnovený objekt a porovná velikost a SHA-256 se zálohou; sady klíčů musí sedět."""
    for bucket, client in clients.items():
        entries = {entry["key"]: entry for entry in read_index(backup, bucket)}
        seen = set()
        for item in list_objects(client, bucket):
            seen.add(item["Key"])
            entry = entries.get(item["Key"])
            if entry is None:
                raise BackupError(f"Bucket {bucket}: objekt {item['Key']!r} navíc.")
            digest = hashlib.sha256()
            size = 0
            for chunk in client.get_object(Bucket=bucket, Key=item["Key"])["Body"].iter_chunks(CHUNK):
                digest.update(chunk)
                size += len(chunk)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise BackupError(f"Bucket {bucket}: objekt {item['Key']!r} po obnově nesedí.")
        if seen != set(entries):
            raise BackupError(f"Bucket {bucket}: po obnově chybí {len(set(entries) - seen)} objektů.")
        print(f"  bucket {bucket}: {len(seen)} objektů, součty sedí")


def cmd_inventory(args):
    """Pomocný výpis pro test: počty řádků tabulek a součty objektů (jen čtení)."""
    env = merged_env(Path(args.workspace))
    result = {"tables": {db: row_counts(db) for db in DATABASES}, "objects": {}}
    for bucket in bucket_credentials(env):
        client = s3_client(env, bucket)
        objects = {}
        for item in list_objects(client, bucket):
            digest = hashlib.sha256()
            for chunk in client.get_object(Bucket=bucket, Key=item["Key"])["Body"].iter_chunks(CHUNK):
                digest.update(chunk)
            objects[item["Key"]] = [item["Size"], digest.hexdigest()]
        result["objects"][bucket] = objects
    json.dump(result, sys.stdout, sort_keys=True)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("copy-objects")
    item.add_argument("--project", required=True)
    item.add_argument("--stamp", required=True)
    item.add_argument("--workspace", default="/workspace")
    for name in ("start", "finish"):
        item = sub.add_parser(name)
        item.add_argument("--project", required=True)
        item.add_argument("--stamp", required=True)
        item.add_argument("--keep", default="7")
        item.add_argument("--workspace", default="/workspace")
        if name == "start":
            item.add_argument("--buckets", default="")
        else:
            item.add_argument("--media", choices=("present", "absent"), required=True)
            item.add_argument("--git-rev", default="unknown")
            item.add_argument("--git-dirty", default="no")
            item.add_argument("--no-env", action="store_true")
    item = sub.add_parser("verify")
    item.add_argument("--backup", required=True)
    item = sub.add_parser("precheck")
    item.add_argument("--project", required=True)
    item.add_argument("--backup", required=True)
    item.add_argument("--workspace", default="/workspace")
    for name in ("restore-check", "restore-objects"):
        item = sub.add_parser(name)
        item.add_argument("--project", required=True)
        item.add_argument("--backup", required=True)
        item.add_argument("--workspace", default="/workspace")
    item = sub.add_parser("inventory")
    item.add_argument("--workspace", default="/workspace")
    return parser


COMMANDS = {
    "start": cmd_start,
    "copy-objects": cmd_copy_objects,
    "finish": cmd_finish,
    "verify": cmd_verify,
    "precheck": cmd_precheck,
    "restore-check": cmd_restore_check,
    "restore-objects": cmd_restore_objects,
    "inventory": cmd_inventory,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.command](args)
    except BackupError as error:
        print(f"CHYBA: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"CHYBA: příkaz {error.cmd[0]} selhal (kód {error.returncode}).", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - např. chyba S3; zpráva bez tajných hodnot
        print(f"CHYBA: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
