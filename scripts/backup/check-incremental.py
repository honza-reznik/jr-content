"""P1-B12: skutečné S3 objekty a kontrola místa pro test-incremental.sh. Jen v Dockeru."""
import json
import shutil
import sys
from pathlib import Path

import jrc_backup as jb

PROJECT = "jrc-p1b12"
workspace = Path("/workspace")
root = jb.backup_root(workspace, PROJECT)
out = workspace / "ops/test-output/P1-B12"
mode = sys.argv[1]
env = jb.merged_env(workspace)


def backups():
    return sorted(p for p in root.iterdir() if jb.STAMP_RE.fullmatch(p.name))


def objects(directory):
    return {(bucket, e["key"]): directory / "s3" / bucket / "objects" / e["file"]
            for bucket in ("jrc-app", "jrc-video", "jrc-local") for e in jb.read_index(directory, bucket)}


if mode == "seed":
    assert not root.exists() or not list(root.iterdir()), "Test vyžaduje prázdnou vlastní složku záloh."
    jb.s3_client(env, "jrc-video").put_object(
        Bucket="jrc-video", Key="p1b12/large.bin", Body=b"media-data-123456" * (1024 * 1024),
        ContentType="application/octet-stream", Metadata={"test": "p1b12"})
    jb.s3_client(env, "jrc-local").put_object(
        Bucket="jrc-local", Key="p1b12/changed.bin", Body=b"A" * (1024 * 1024))
elif mode == "unchanged":
    first, second = backups()
    for directory in (first, second):
        jb.validate_backup(directory)
    old, new = objects(first), objects(second)
    assert old.keys() == new.keys()
    assert all(old[k].stat().st_ino == new[k].stat().st_ino for k in old)
    one = sum(p.stat().st_blocks * 512 for p in old.values())
    unique = {(p.stat().st_dev, p.stat().st_ino): p.stat().st_blocks * 512
              for p in [*old.values(), *new.values()]}
    both = sum(unique.values())
    assert one == both and one >= 17 * 1024 * 1024
    print(f"OK: jedna sada médií {one} B; obě zálohy dohromady {both} B, {len(old)} objektů.")
    (out / "backups.json").write_text(json.dumps({"first": first.name, "second": second.name}))
elif mode == "change":
    jb.s3_client(env, "jrc-local").put_object(
        Bucket="jrc-local", Key="p1b12/changed.bin", Body=b"B" * (1024 * 1024))
elif mode == "changed":
    first, second, third = backups()
    old, new = objects(second), objects(third)
    changed = ("jrc-local", "p1b12/changed.bin")
    assert old[changed].read_bytes() == b"A" * (1024 * 1024)
    assert new[changed].read_bytes() == b"B" * (1024 * 1024)
    assert old[changed].stat().st_ino != new[changed].stat().st_ino
    assert all(old[k].stat().st_ino == new[k].stat().st_ino for k in old if k != changed)
    jb.validate_backup(third)
    shutil.rmtree(first)
    jb.validate_backup(second)
    jb.validate_backup(third)
    print("OK: změněný objekt má samostatná data, ostatní sdílejí místo; první záloha smazána, druhá a třetí platné.")
elif mode == "restored":
    before = json.loads((out / "before.json").read_text())
    after = json.loads((out / "after.json").read_text())
    assert before["objects"] == after["objects"], "Objekty po obnově neodpovídají druhé záloze."
    volatile = {"public.background_task", "public.background_task_completedtask", "public.django_session"}
    stable = lambda inv: {db: {k: v for k, v in rows.items() if k not in volatile}
                          for db, rows in inv["tables"].items()}
    assert stable(before) == stable(after), "Databáze po obnově neodpovídají druhé záloze."
    print("OK: obě databáze a všechny objekty po obnově druhé zálohy odpovídají původnímu inventáři.")
else:
    raise SystemExit("Neznámý krok testu.")
