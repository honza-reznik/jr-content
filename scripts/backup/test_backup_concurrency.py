"""P1-B12: dva skutečné procesy backup.sh; Docker, DB a S3 nahrazené jen v testu."""
import fcntl
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import jrc_backup as jb
import test_jrc_backup as fixtures
from test_jrc_backup import Base, make_backup

SCRIPTS = Path(__file__).resolve().parent
DATA = b"obsah-objektu"
ETAG = '"test-etag"'


def wait_for(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Chybí testovací značka {path}")
        time.sleep(0.01)


def fake_docker(argv):
    """Řízení zálohy je skutečné, Python kopíruje/ověřuje/rotuje skutečné soubory."""
    run = os.environ["RUN_ID"]
    work = Path.cwd()
    if argv[:2] in (["image", "inspect"], ["volume", "ls"]):
        return 0
    assert argv[0] == "compose", argv
    argv = argv[argv.index("exec") + 2:]
    service, *command = argv
    if service == "jrc-s3":
        print("ID                Created     Global aliases  Local aliases")
        for bucket in ("jrc-app", "jrc-video", "jrc-local"):
            print(f"id  2026-11-04  {bucket}")
        return 0
    if service == "jrc-postgres":
        if "--list" in command:
            print("TABLE DATA")
        elif "--data-only" in command:
            print("COPY public.example (id) FROM stdin;\n1\n\\.")
        else:
            print("test database dump")
        return 0
    assert service == "jrc-dev", argv
    operation = command[2]
    if operation == "start":
        (work / f"{run}-started").touch()
        if os.environ.get("FAIL_START") == "1":
            return 42

    def pause(stage):
        marker = work / f"{run}-{stage}"
        if run == "A" and not marker.exists():
            marker.touch()
            wait_for(work / f"release-{stage}")

    real_link = jb.link_or_copy
    real_rotate = jb.rotate

    def link(source, target):
        pause("copy")  # Zdroj je vybraný a index přečtený, ale odkaz ještě nevznikl.
        return real_link(source, target)

    def rotate(*args, **kwargs):
        pause("rotate")  # Zámek musí platit i po přejmenování .partial.
        return real_rotate(*args, **kwargs)

    client = mock.Mock()
    client.exceptions.NoSuchKey = type("Missing", (Exception,), {})
    client.head_object.return_value = {"ContentLength": len(DATA), "ETag": ETAG}
    client.get_object.side_effect = AssertionError("Nezměněný objekt se nemá stahovat.")
    with mock.patch.object(jb, "merged_env", return_value=fixtures.RestoreEmptinessGuards.ENV), \
            mock.patch.object(jb, "s3_client", return_value=client), \
            mock.patch.object(jb, "list_objects", side_effect=lambda c, b: [
                {"Key": "a/b.txt", "Size": len(DATA)}] if b == "jrc-app" else []), \
            mock.patch.object(jb, "psql", return_value="0"), \
            mock.patch.object(jb, "pg_version", return_value="18.6"), \
            mock.patch.object(jb, "link_or_copy", side_effect=link), \
            mock.patch.object(jb, "rotate", side_effect=rotate):
        return jb.main([*command[2:], "--workspace", str(work)])


class BackupConcurrency(Base):
    def setUp(self):
        super().setUp()
        self.root = self.work / "backups/jrc-p1b12"
        self.old = make_backup(self.root, "20261024-100000")
        index = self.old / "s3/jrc-app/index.json"
        entries = jb.read_index(self.old, "jrc-app")
        entries[0]["etag"] = ETAG
        index.write_text(jb.json.dumps(entries))
        manifest = jb.load_manifest(self.old)
        digest, size = jb.sha256_file(index)
        manifest["files"]["s3/jrc-app/index.json"] = {"sha256": digest, "size": size}
        (self.old / jb.MANIFEST).write_text(jb.json.dumps(manifest))
        self.inode = (self.old / "s3/jrc-app/objects/00000001").stat().st_ino
        scripts = self.work / "scripts/backup"
        scripts.mkdir(parents=True)
        shutil.copy(SCRIPTS / "backup.sh", scripts)
        (scripts.parent / "workspace.sh").write_text("exit 0\n")
        bin_dir = self.work / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                          f"{shlex.quote(str(Path(__file__).resolve()))} docker \"$@\"\n")
        docker.chmod(0o700)
        flock = bin_dir / "flock"
        real_flock = shlex.quote(shutil.which("flock"))
        flock.write_text(f'''#!/bin/sh
if [ "${{PAUSE_CONTENDER:-}}" = 1 ]; then
    if {real_flock} -n "$@"; then touch B-unexpected-lock; else touch B-blocked; fi
    touch "$RUN_ID-at-lock"
    while [ ! -f release-contender ]; do sleep 0.01; done
fi
exec {real_flock} "$@"
''')
        flock.chmod(0o700)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                        JRC_PROJECT=self.root.name, JRC_BACKUP_KEEP="1", JRC_BACKUP_NO_ENV="1")

    def start(self, run, **env):
        process = subprocess.Popen(["sh", "scripts/backup/backup.sh", "backup"], cwd=self.work,
                                   env=dict(self.env, RUN_ID=run, **env), start_new_session=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def cleanup():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()

        self.addCleanup(cleanup)
        return process

    def assert_success(self, process):
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, stdout + stderr)

    def assert_lock_held(self):
        with (self.root / ".backup.lock").open("a") as lock:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_concurrent_backups_keep_one_protect_source_through_rotation(self):
        first = self.start("A")
        wait_for(self.work / "A-copy")
        second = self.start("B", PAUSE_CONTENDER="1")
        # Druhý proces už skutečně zkusil zámek; před opakováním čeká na značku.
        # Proto kontrola u rotace nemůže omylem vidět zámek držený druhým během.
        wait_for(self.work / "B-at-lock")
        self.assertTrue((self.work / "B-blocked").exists())
        self.assertFalse((self.work / "B-unexpected-lock").exists())
        self.assert_lock_held()
        self.assertFalse((self.work / "B-started").exists())
        self.assertTrue(self.old.exists())
        (self.work / "release-copy").touch()
        wait_for(self.work / "A-rotate")
        self.assert_lock_held()
        self.assertFalse((self.work / "B-started").exists())
        self.assertTrue(self.old.exists())
        (self.work / "release-rotate").touch()
        (self.work / "release-contender").touch()
        self.assert_success(first)
        self.assert_success(second)
        self.assertFalse(self.old.exists())
        self.assertEqual(list(self.root.glob("*.partial")), [])
        final, = [p for p in self.root.iterdir() if jb.STAMP_RE.fullmatch(p.name)]
        jb.validate_backup(final)
        obj = final / "s3/jrc-app/objects/00000001"
        self.assertEqual(obj.read_bytes(), DATA)
        self.assertEqual(obj.stat().st_ino, self.inode)

    def test_failed_backup_releases_lock_without_replacing_its_inode(self):
        failed = self.start("A", FAIL_START="1")
        failed.communicate(timeout=10)
        self.assertEqual(failed.returncode, 42)
        lock = self.root / ".backup.lock"
        inode = lock.stat().st_ino
        self.assert_success(self.start("B"))
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_symlink_lock_is_rejected_without_writing_its_target(self):
        target = self.work / "foreign"
        target.write_text("cizí data")
        (self.root / ".backup.lock").symlink_to(target)
        rejected = self.start("B")
        _, stderr = rejected.communicate(timeout=10)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("Symbolický odkaz", stderr)
        self.assertFalse((self.work / "B-started").exists())
        self.assertEqual(target.read_text(), "cizí data")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "docker":
        sys.exit(fake_docker(sys.argv[2:]))
    unittest.main()
