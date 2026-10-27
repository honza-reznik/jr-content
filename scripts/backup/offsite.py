#!/usr/bin/env python3
"""Šifrování a obnova databázové části offsite záloh (P1-B04), pouze uvnitř jrc-dev."""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

DATABASES = ("brightbean", "freeframe")
STAMP = re.compile(r"^[0-9]{8}-[0-9]{6}(?:-[0-9]+)?$")


def fail(message):
    raise SystemExit(f"CHYBA: {message}")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest(), path.stat().st_size


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def make_archive(backup, repo, recipient, keep, remote):
    backup = Path(backup).resolve(strict=True)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    stamp = manifest.get("stamp")
    if manifest.get("complete") is not True or not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
        fail("Místní záloha nemá platný dokončený manifest.")
    expected = {"manifest.json", *(f"postgres/{db}.dump" for db in DATABASES)}
    if not all((backup / name).is_file() for name in expected):
        fail("Místní záloze chybí manifest nebo některý databázový dump.")
    repo = Path(repo).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    archive = repo / f"{stamp}.age"
    if archive.exists() or archive.is_symlink():
        fail(f"Offsite soubor již existuje: {archive.name}")
    if (repo / ".git").exists():
        git(repo, "status", "--porcelain", check_clean=True)
        branch = subprocess.check_output(["git", "-C", str(repo), "branch", "--show-current"], text=True).strip()
        if branch != "zalohy":
            if branch not in {"main", "master"}:
                fail("JRC_BACKUP_GIT_DIR musí být na větvi main, master nebo zalohy.")
            run("git", "-C", str(repo), "switch", "--orphan", "zalohy")
            run("git", "-C", str(repo), "rm", "-rf", "--ignore-unmatch", ".")
        if remote and "origin" not in subprocess.check_output(["git", "-C", str(repo), "remote"], text=True).split():
            git(repo, "remote", "add", "origin", remote)
    elif remote:
        if any(repo.iterdir()):
            fail("JRC_BACKUP_GIT_DIR není prázdný klon git repozitáře.")
        # Klonování zachová hlavní větev kódu; zálohy se zapisují do samostatné historie.
        with tempfile.TemporaryDirectory(prefix="jrc-backup-clone-") as temporary:
            clone = Path(temporary) / "clone"
            run("git", "clone", remote, str(clone))
            shutil.copytree(clone, repo, symlinks=True, dirs_exist_ok=True)
    else:
        if any(repo.iterdir()):
            fail("JRC_BACKUP_GIT_DIR musí být prázdný nebo git repozitář.")
        run("git", "init", "--initial-branch=zalohy", str(repo))
    if (repo / ".git").exists():
        branch = subprocess.check_output(["git", "-C", str(repo), "branch", "--show-current"], text=True).strip()
        if branch != "zalohy":
            run("git", "-C", str(repo), "switch", "--orphan", "zalohy")
            run("git", "-C", str(repo), "rm", "-rf", "--ignore-unmatch", ".")
    git(repo, "config", "user.name", "JR Content backups")
    git(repo, "config", "user.email", "backups@localhost")

    with tempfile.TemporaryDirectory(prefix="jrc-offsite-") as temporary:
        payload = Path(temporary) / "payload.tar"
        with tarfile.open(payload, "w", format=tarfile.PAX_FORMAT) as tar:
            for name in sorted(expected):
                tar.add(backup / name, arcname=name, recursive=False)
        with payload.open("rb") as source:
            run("age", "--recipient", recipient, "--output", str(archive), stdin=source)
    archive.chmod(0o600)

    files = sorted(p for p in repo.glob("*.age") if p.is_file() and not p.is_symlink())
    for old in files[:-keep]:
        old.unlink()
    git(repo, "add", "--", "*.age")
    git(repo, "add", "-u", "--", "*.age")
    git(repo, "commit", "--allow-empty", "-m", f"Záloha databází {stamp}")
    if remote:
        git(repo, "push", "--set-upstream", "origin", "zalohy")
    print(f"Uložena šifrovaná databázová záloha {stamp}.age; historie git zachována.")


def git(repo, *args, check_clean=False):
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    if check_clean and result.stdout.strip():
        fail("JRC_BACKUP_GIT_DIR obsahuje neuložené změny; nic jsem nepřepsal.")
    if result.returncode:
        fail(f"git {args[0]} selhal: {result.stderr.strip()}")
    return result.stdout


def safe_members(tar):
    expected = {"manifest.json", *(f"postgres/{db}.dump" for db in DATABASES)}
    members = tar.getmembers()
    names = {item.name for item in members}
    if names != expected or any(not item.isfile() or item.issym() or item.islnk() for item in members):
        fail("Šifrovaná záloha obsahuje nepovolený obsah.")
    return members


def restore(archive, identity, output):
    archive, identity, output = Path(archive), Path(identity), Path(output)
    if not archive.is_file() or archive.is_symlink() or not identity.is_file() or identity.is_symlink():
        fail("Šifrovaný soubor nebo soukromý klíč není běžný soubor.")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jrc-offsite-restore-") as temporary:
        plain = Path(temporary) / "payload.tar"
        run("age", "--decrypt", "--identity", str(identity), "--output", str(plain), str(archive))
        with tarfile.open(plain, "r:") as tar:
            safe_members(tar)
            manifest = json.load(tar.extractfile("manifest.json"))
            stamp = manifest.get("stamp")
            if manifest.get("complete") is not True or not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
                fail("Dešifrovaný manifest není platný.")
            target = output / stamp
            if target.exists() or target.is_symlink():
                fail(f"Cílová místní záloha už existuje: {target}")
            target.mkdir(mode=0o700)
            for member in tar.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    fail("Záloha obsahuje nebezpečnou cestu.")
                destination = target / name
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with tar.extractfile(member) as source, destination.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
            # F2-B09 formát: databáze se obnoví, tři S3 buckety a média vzniknou prázdné.
            (target / "postgres").mkdir(mode=0o700, exist_ok=True)
            files = {}
            for db in DATABASES:
                dump = target / "postgres" / f"{db}.dump"
                files[f"postgres/{db}.dump"] = {"sha256": digest(dump)[0], "size": dump.stat().st_size}
                checkpoint = {"dump": files[f"postgres/{db}.dump"], "buckets": {"jrc-app" if db == "brightbean" else "jrc-video": []}}
                path = target / "postgres" / f"{db}.references.json"
                path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
                files[f"postgres/{db}.references.json"] = {"sha256": digest(path)[0], "size": path.stat().st_size}
            for bucket in ("jrc-app", "jrc-video", "jrc-local"):
                folder = target / "s3" / bucket
                (folder / "objects").mkdir(mode=0o700, parents=True)
                index = folder / "index.json"
                index.write_text("[]\n", encoding="utf-8")
                files[f"s3/{bucket}/index.json"] = {"sha256": digest(index)[0], "size": index.stat().st_size}
            original = manifest
            manifest = dict(original)
            manifest["files"] = files
            manifest["buckets"] = {name: {"objects": 0, "bytes": 0} for name in ("jrc-app", "jrc-video", "jrc-local")}
            manifest["bb_media"] = {"included": False, "bytes": 0}
            manifest["env_included"] = False
            manifest["offsite_database_only"] = True
            manifest["notes"] = "Obnoveno z offsite zálohy databází; S3 objekty, média a .env nebyly součástí zálohy."
            (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            for path in target.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
    print(f"Databázová záloha obnovena do {target}; S3 objekty, média a .env nejsou obsaženy.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("--identity")
    args = parser.parse_args()
    if args.operation == "backup":
        make_archive(os.environ["JRC_OFFSITE_BACKUP"], os.environ["JRC_BACKUP_GIT_DIR"],
                     os.environ["JRC_BACKUP_AGE_RECIPIENT"], int(os.environ["JRC_BACKUP_KEEP_OFFSITE"]),
                     os.environ.get("JRC_BACKUP_GIT_REMOTE", ""))
    else:
        if not args.identity:
            fail("Obnova potřebuje soukromý klíč age.")
        restore(os.environ["JRC_OFFSITE_ARCHIVE"], args.identity, os.environ["JRC_OFFSITE_OUTPUT"])


if __name__ == "__main__":
    main()
