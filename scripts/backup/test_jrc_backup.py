"""Jednotkové testy pojistek záloh a obnovy (F2-B09). Nepotřebují běžící služby ani síť."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import jrc_backup as jb


def make_backup(project_root, stamp, project=None, payload=b"data", complete=True, tamper_manifest=None):
    """Úplná testovací záloha obou DB a všech tří bucketů."""
    project_root = Path(project_root)
    project = project or project_root.name
    directory = project_root / stamp
    (directory / "postgres").mkdir(parents=True)
    files = {}
    buckets = {}
    for db in jb.DATABASES:
        (directory / "postgres" / f"{db}.dump").write_bytes(payload)
        bucket = "jrc-app" if db == "brightbean" else "jrc-video"
        (directory / "postgres" / f"{db}.references.json").write_text(json.dumps({
            "dump": {"size": len(payload), "sha256": jb.hashlib.sha256(payload).hexdigest()}, "buckets": {bucket: []}}))
    object_data = b"obsah-objektu"
    for bucket in ("jrc-app", "jrc-video", "jrc-local"):
        folder = directory / "s3" / bucket
        (folder / "objects").mkdir(parents=True)
        entries = []
        if bucket == "jrc-app":
            (folder / "objects" / "00000001").write_bytes(object_data)
            entries = [{"key": "a/b.txt", "file": "00000001", "size": len(object_data),
                        "sha256": jb.hashlib.sha256(object_data).hexdigest()}]
        (folder / "index.json").write_text(json.dumps(entries))
        buckets[bucket] = {"objects": len(entries), "bytes": sum(e["size"] for e in entries)}
    env_included = bool(tamper_manifest and tamper_manifest.get("env_included"))
    if env_included:
        (directory / "env.backup").write_text(EnvHandling.SAVED)
    for path in directory.rglob("*"):
        if path.is_file() and "/objects/" not in str(path):
            digest, size = jb.sha256_file(path)
            files[path.relative_to(directory).as_posix()] = {"size": size, "sha256": digest}
    manifest = {
        "format": jb.FORMAT, "complete": complete, "project": project, "stamp": stamp,
        "created_at": jb.utc_now(), "code": {"git_rev": "test", "dirty": False},
        "postgres": {"server_version": "18.6", "databases": {db: len(payload) for db in jb.DATABASES}},
        "buckets": buckets, "files": files, "vanished_during_backup": {},
        "bb_media": {"included": False, "bytes": 0}, "env_included": env_included,
    }
    if tamper_manifest:
        manifest.update(tamper_manifest)
    (directory / jb.MANIFEST).write_text(json.dumps(manifest))
    return directory


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.root = self.work / "backups" / "jrc-test"
        self.root.mkdir(parents=True)
        self.rotation = mock.patch.object(jb.Path, "cwd", return_value=self.work)
        self.rotation.start()
        self.addCleanup(self.rotation.stop)


class ProjectGuards(Base):
    def test_restore_refuses_main_project(self):
        with self.assertRaises(jb.BackupError) as raised:
            jb.check_restore_project("jrc")
        self.assertIn("hlavního projektu jrc", str(raised.exception))

    def test_restore_accepts_only_verification_projects(self):
        self.assertEqual(jb.check_restore_project("jrc-f2b09r"), "jrc-f2b09r")
        for bad in ("", "jrcx", "JRC-x", "jrc-", "jrc_x", "jrc-x y", "jrc-x;rm", "../jrc", "jrc-X", None):
            with self.assertRaises(jb.BackupError, msg=repr(bad)):
                jb.check_restore_project(bad)

    def test_backup_project_name_validation(self):
        self.assertEqual(jb.check_project("jrc"), "jrc")
        self.assertEqual(jb.check_project("jrc-a-1"), "jrc-a-1")
        for bad in ("jrcx", "x", "jrc-", "jrc/..", "jrc-A"):
            with self.assertRaises(jb.BackupError, msg=bad):
                jb.check_project(bad)

    def test_keep_validation(self):
        self.assertEqual(jb.check_keep("7"), 7)
        for bad in ("0", "-1", "abc", "", "1.5", "07", " "):
            with self.assertRaises(jb.BackupError, msg=repr(bad)):
                jb.check_keep(bad)

    def test_cli_restore_data_and_precheck_refuse_main_before_anything(self):
        for command in ("precheck", "restore-check", "restore-objects"):
            argv = [command, "--project", "jrc", "--backup", str(self.work / "neexistuje")]
            with mock.patch.object(jb, "s3_client", side_effect=AssertionError("nesmí sahat na S3")), \
                    mock.patch.object(jb, "psql", side_effect=AssertionError("nesmí sahat na databázi")), \
                    mock.patch("subprocess.run", side_effect=AssertionError("nesmí spouštět nic")):
                self.assertEqual(jb.main(argv), 1)


class Validation(Base):
    def test_valid_backup(self):
        directory = make_backup(self.root, "20261024-100000")
        manifest = jb.validate_backup(directory)
        self.assertEqual(manifest["project"], "jrc-test")
        self.assertEqual(jb.verify_objects(directory, manifest, log=lambda *_: None), 1)

    def test_missing_manifest_and_incomplete(self):
        empty = self.root / "20261024-100001"
        empty.mkdir()
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(empty)
        directory = make_backup(self.root, "20261024-100002", complete=False)
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)

    def test_wrong_format_and_garbage_manifest(self):
        directory = make_backup(self.root, "20261024-100003", tamper_manifest={"format": "x"})
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)
        (directory / jb.MANIFEST).write_text("{nejde json")
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)

    def test_corrupted_file_is_detected_by_checksum_not_by_size(self):
        directory = make_backup(self.root, "20261024-100004", payload=b"abcd")
        (directory / "postgres" / "brightbean.dump").write_bytes(b"abce")  # stejná velikost
        self.assertFalse(jb.is_valid_backup(directory))  # rotace musí zkontrolovat i součty
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)

    def test_missing_and_resized_file(self):
        directory = make_backup(self.root, "20261024-100005")
        (directory / "postgres" / "brightbean.dump").write_bytes(b"mnohem delsi obsah")
        self.assertFalse(jb.is_valid_backup(directory))
        (directory / "postgres" / "brightbean.dump").unlink()
        self.assertFalse(jb.is_valid_backup(directory))

    def test_path_traversal_in_manifest_rejected(self):
        directory = make_backup(self.root, "20261024-100006")
        manifest = json.loads((directory / jb.MANIFEST).read_text())
        manifest["files"]["../../etc/passwd"] = {"size": 1, "sha256": "x"}
        (directory / jb.MANIFEST).write_text(json.dumps(manifest))
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)
        manifest["files"] = {"/etc/passwd": {"size": 1, "sha256": "x"}}
        (directory / jb.MANIFEST).write_text(json.dumps(manifest))
        with self.assertRaises(jb.BackupError):
            jb.validate_backup(directory)

    def test_corrupted_object_is_detected(self):
        directory = make_backup(self.root, "20261024-100007")
        manifest = jb.validate_backup(directory)
        (directory / "s3" / "jrc-app" / "objects" / "00000001").write_bytes(b"jiny-obsah-objekt")
        with self.assertRaises(jb.BackupError):
            jb.verify_objects(directory, manifest, log=lambda *_: None)

    def test_extra_object_file_is_detected(self):
        directory = make_backup(self.root, "20261024-100008")
        manifest = jb.validate_backup(directory)
        (directory / "s3" / "jrc-app" / "objects" / "00000002").write_bytes(b"x")
        with self.assertRaises(jb.BackupError):
            jb.verify_objects(directory, manifest, log=lambda *_: None)

    def test_symlinked_backup_directory_rejected(self):
        real = make_backup(self.root, "20261024-100009")
        link = self.root / "20261024-100010"
        link.symlink_to(real)
        with self.assertRaises(jb.BackupError):
            jb.load_manifest(link)


class Rotation(Base):
    def stamps(self):
        return sorted(p.name for p in self.root.iterdir())

    def test_keeps_newest_n_valid(self):
        for i in range(5):
            make_backup(self.root, f"20261024-10000{i}")
        removed = jb.rotate(self.root, 2, log=lambda *_: None)
        self.assertEqual(removed, ["20261024-100000", "20261024-100001", "20261024-100002"])
        self.assertEqual(self.stamps(), ["20261024-100003", "20261024-100004"])

    def test_never_deletes_without_valid_manifest(self):
        make_backup(self.root, "20261024-100000")
        make_backup(self.root, "20261024-100001")
        make_backup(self.root, "20261024-100002")
        # starší adresář bez manifestu, rozpracovaná záloha, cizí složka, soubor, nedokončený manifest
        (self.root / "20260101-000000").mkdir()
        (self.root / "20260101-000000" / "dulezite.txt").write_text("ne")
        (self.root / "20261024-090000.partial").mkdir()
        (self.root / "moje-poznamky").mkdir()
        (self.root / "20260102-000000.txt").write_text("x")
        make_backup(self.root, "20260103-000000", complete=False)
        jb.rotate(self.root, 1, log=lambda *_: None)
        left = self.stamps()
        for kept in ("20260101-000000", "20261024-090000.partial", "moje-poznamky", "20260102-000000.txt", "20260103-000000", "20261024-100002"):
            self.assertIn(kept, left)
        self.assertNotIn("20261024-100000", left)
        self.assertNotIn("20261024-100001", left)
        self.assertTrue((self.root / "20260101-000000" / "dulezite.txt").exists())

    def test_manifest_of_other_project_or_stamp_is_not_deleted(self):
        make_backup(self.root, "20261024-100000", project="jrc-jiny")
        make_backup(self.root, "20261024-100001", tamper_manifest={"stamp": "20261024-999999"})
        make_backup(self.root, "20261024-100002")
        jb.rotate(self.root, 1, log=lambda *_: None)
        self.assertEqual(self.stamps(), ["20261024-100000", "20261024-100001", "20261024-100002"])

    def test_invalid_keep_deletes_nothing(self):
        make_backup(self.root, "20261024-100000")
        make_backup(self.root, "20261024-100001")
        for bad in (0, -3, "x", ""):
            with self.assertRaises(jb.BackupError):
                jb.rotate(self.root, bad)
        self.assertEqual(len(self.stamps()), 2)

    def test_refuses_directory_outside_backups_project_layout(self):
        outside = self.work / "nejaka-slozka"
        outside.mkdir()
        make_backup(outside, "20261024-100000")
        make_backup(outside, "20261024-100001")
        with self.assertRaises(jb.BackupError):
            jb.rotate(outside, 1)
        self.assertTrue((outside / "20261024-100000").exists())
        wrong_name = self.work / "backups" / "ne-projekt"
        wrong_name.mkdir()
        with self.assertRaises(jb.BackupError):
            jb.rotate(wrong_name, 1)

    def test_symlinked_project_root_refused_and_symlinked_backup_untouched(self):
        real = make_backup(self.root, "20261024-100000")
        make_backup(self.root, "20261024-100001")
        link_root = self.work / "backups" / "jrc-odkaz"
        link_root.symlink_to(self.root)
        with self.assertRaises(jb.BackupError):
            jb.rotate(link_root, 1)
        target = self.work / "cizi-data"
        target.mkdir()
        (target / "soubor").write_text("ne")
        (self.root / "20261024-100002").symlink_to(target)
        jb.rotate(self.root, 1, log=lambda *_: None)
        self.assertTrue((target / "soubor").exists())
        self.assertTrue(real.parent.exists())

    def test_keep_larger_than_count_deletes_nothing(self):
        make_backup(self.root, "20261024-100000")
        self.assertEqual(jb.rotate(self.root, 7, log=lambda *_: None), [])


class EnvHandling(Base):
    SAVED = "PGPASSWORD=tajne1\nJRC_BB_SECRET_KEY=tajne2\nJRC_APP_PORT=13303\nJRC_UID=1000\nJRC_S3_APP_QUOTA=20GiB\n"

    def test_compare_ignores_ports_and_ids_and_never_returns_values(self):
        saved = jb.load_env_file(self._write("saved", self.SAVED))
        current = dict(saved, JRC_APP_PORT="13313", JRC_UID="1001")
        self.assertEqual(jb.compare_env(current, saved), [])
        current["PGPASSWORD"] = "jine-heslo"
        differing = jb.compare_env(current, saved)
        self.assertEqual(differing, ["PGPASSWORD"])
        self.assertNotIn("tajne1", " ".join(differing))

    def test_missing_secret_counts_as_difference(self):
        saved = jb.load_env_file(self._write("saved", self.SAVED))
        self.assertEqual(jb.compare_env({"PGPASSWORD": "tajne1"}, saved), ["JRC_BB_SECRET_KEY"])

    def test_install_env_sets_ports_mode_600_and_never_overwrites(self):
        saved = self._write("saved", self.SAVED)
        target = self.work / ".env"
        jb.install_env(saved, target, {"JRC_APP_PORT": "13313", "JRC_S3_PORT": "14033", "JRC_BB_PORT": ""})
        content = target.read_text()
        self.assertIn("JRC_APP_PORT=13313\n", content)
        self.assertIn("JRC_S3_PORT=14033\n", content)
        self.assertIn("PGPASSWORD=tajne1\n", content)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(jb.BackupError):
            jb.install_env(saved, target, {})

    def test_precheck_rejects_foreign_secrets(self):
        directory = make_backup(self.root, "20261024-100000", tamper_manifest={"env_included": True})
        # manifest se změnil -> přepočítat součty není potřeba, env.backup není v seznamu souborů
        (directory / "env.backup").write_text(self.SAVED)
        workspace = self.work / "ws"
        workspace.mkdir()
        (workspace / ".env").write_text("PGPASSWORD=jiny\nJRC_BB_SECRET_KEY=tajne2\n")
        args = ["precheck", "--project", "jrc-cil", "--backup", str(directory), "--workspace", str(workspace)]
        self.assertEqual(jb.main(args), 1)
        (workspace / ".env").write_text("PGPASSWORD=tajne1\nJRC_BB_SECRET_KEY=tajne2\nJRC_APP_PORT=1\n")
        self.assertEqual(jb.main(args), 0)

    def test_precheck_installs_env_when_missing(self):
        directory = make_backup(self.root, "20261024-100000", tamper_manifest={"env_included": True})
        (directory / "env.backup").write_text(self.SAVED)
        workspace = self.work / "ws"
        workspace.mkdir()
        with mock.patch.dict(os.environ, {"JRC_APP_PORT": "13313"}):
            self.assertEqual(jb.main(["precheck", "--project", "jrc-cil", "--backup", str(directory), "--workspace", str(workspace)]), 0)
        self.assertIn("JRC_APP_PORT=13313", (workspace / ".env").read_text())
        self.assertEqual(stat.S_IMODE((workspace / ".env").stat().st_mode), 0o600)

    def test_precheck_without_any_env_refused(self):
        directory = make_backup(self.root, "20261024-100000")
        workspace = self.work / "ws"
        workspace.mkdir()
        self.assertEqual(jb.main(["precheck", "--project", "jrc-cil", "--backup", str(directory), "--workspace", str(workspace)]), 1)

    def _write(self, name, text):
        path = self.work / name
        path.write_text(text)
        return path


class RestoreEmptinessGuards(Base):
    ENV = {
        "AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b", "JRC_FF_S3_ACCESS_KEY": "c",
        "JRC_FF_S3_SECRET_KEY": "d", "JRC_BB_S3_ACCESS_KEY": "e", "JRC_BB_S3_SECRET_KEY": "f",
    }

    def run_command(self, command, db_empty=True, bucket_empty=True):
        directory = self.root / "20261024-100000"
        if not directory.exists():
            make_backup(self.root, "20261024-100000")
        workspace = self.work / "ws"
        workspace.mkdir(exist_ok=True)
        client = mock.Mock()
        patches = [
            mock.patch.dict(os.environ, self.ENV),
            mock.patch.object(jb, "s3_client", return_value=client),
            mock.patch.object(jb, "ensure_database"),
            mock.patch.object(jb, "database_is_empty", return_value=db_empty),
            mock.patch.object(jb, "bucket_is_empty", return_value=bucket_empty),
            mock.patch.object(jb, "verify_restored_objects"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        code = jb.main([command, "--project", "jrc-cil", "--backup", str(directory), "--workspace", str(workspace)])
        return code, client

    def test_nonempty_database_refused(self):
        self.assertEqual(self.run_command("restore-check", db_empty=False)[0], 1)

    def test_nonempty_bucket_refused_by_check_and_never_uploaded_into(self):
        self.assertEqual(self.run_command("restore-check", bucket_empty=False)[0], 1)
        code, client = self.run_command("restore-objects", bucket_empty=False)
        self.assertEqual(code, 1)
        client.upload_file.assert_not_called()

    def test_empty_target_passes_and_uploads(self):
        self.assertEqual(self.run_command("restore-check")[0], 0)
        code, client = self.run_command("restore-objects")
        self.assertEqual(code, 0)
        self.assertEqual(client.upload_file.call_count, 1)


if __name__ == "__main__":
    unittest.main()
