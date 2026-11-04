"""P1-B12: úspora místa, samostatnost záloh a zachování pojistek při opakovaném čtení S3."""
import errno
import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jrc_backup as jb
import offsite
import test_jrc_backup as fixtures
from test_jrc_backup import Base, make_backup


class Incremental(Base):
    def setUp(self):
        super().setUp()
        self.old = make_backup(self.root, "20261024-100000")
        self.index = self.old / "s3/jrc-app/index.json"
        entries = jb.read_index(self.old, "jrc-app")
        self.data = (self.old / "s3/jrc-app/objects/00000001").read_bytes()
        self.etag = '"test-etag"'
        entries[0]["etag"] = self.etag
        self.index.write_text(json.dumps(entries))
        self.rehash_index()
        self.saved = entries[0]
        self.source = self.old / "s3/jrc-app/objects/00000001"
        self.target = self.work / "new-object"
        self.client = mock.Mock()
        self.client.exceptions.NoSuchKey = type("Missing", (Exception,), {})
        self.set_response(self.data, self.etag)

    def rehash_index(self):
        manifest = jb.load_manifest(self.old)
        digest, size = jb.sha256_file(self.index)
        manifest["files"]["s3/jrc-app/index.json"] = {"sha256": digest, "size": size}
        (self.old / jb.MANIFEST).write_text(json.dumps(manifest))

    def set_response(self, data, etag, **metadata):
        head = {"ContentLength": len(data), **metadata}
        if etag:
            head["ETag"] = etag
        self.client.head_object.return_value = head
        body = mock.Mock()
        body.iter_chunks.return_value = [data]
        self.client.get_object.return_value = {**head, "Body": body}

    def copy(self):
        return jb.copy_object(self.client, "jrc-app", self.saved["key"], self.target,
                              self.saved, self.source)

    def test_unchanged_object_is_linked_without_get(self):
        entry, reused = self.copy()
        self.assertTrue(reused)
        self.assertEqual(entry["sha256"], self.saved["sha256"])
        self.assertEqual(self.target.stat().st_ino, self.source.stat().st_ino)
        self.client.get_object.assert_not_called()

    def test_changed_same_size_object_is_downloaded_to_own_inode(self):
        data = b"x" * len(self.data)
        self.set_response(data, '"new-etag"')
        entry, reused = self.copy()
        self.assertFalse(reused)
        self.assertNotEqual(self.target.stat().st_ino, self.source.stat().st_ino)
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        self.client.get_object.assert_called_once_with(Bucket="jrc-app", Key="a/b.txt", IfMatch='"new-etag"')
        self.client.get_object.return_value["Body"].close.assert_called_once()

    def test_size_change_with_same_etag_is_not_reused(self):
        self.set_response(b"longer-content-different-size", self.etag)
        self.assertFalse(self.copy()[1])
        self.client.get_object.assert_called_once()

    def test_old_backup_without_etag_reuses_by_checksum(self):
        self.saved.pop("etag")
        self.assertTrue(self.copy()[1])
        self.client.get_object.assert_called_once()
        self.assertEqual(self.target.stat().st_ino, self.source.stat().st_ino)

    def test_missing_etag_in_response_never_skips_download(self):
        self.set_response(self.data, None)
        entry, reused = self.copy()
        self.assertTrue(reused)
        self.assertNotIn("etag", entry)
        self.client.get_object.assert_called_once_with(Bucket="jrc-app", Key="a/b.txt")

    def test_metadata_change_reuses_bytes_but_keeps_current_metadata(self):
        self.set_response(self.data, self.etag, ContentType="video/mp4", Metadata={"test": "new"})
        entry, reused = self.copy()
        self.assertTrue(reused)
        self.assertEqual(entry["Metadata"], {"test": "new"})
        self.assertEqual(entry["ContentType"], "video/mp4")
        self.client.get_object.assert_not_called()

    def test_unsupported_hardlink_falls_back_to_independent_copy(self):
        for code in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK):
            with self.subTest(code=code), mock.patch.object(jb.os, "link", side_effect=OSError(code, "test")):
                self.copy()
                self.assertNotEqual(self.target.stat().st_ino, self.source.stat().st_ino)
                self.assertEqual(self.target.read_bytes(), self.data)
                self.target.unlink()

    def test_disk_full_does_not_silently_fall_back(self):
        with mock.patch.object(jb.os, "link", side_effect=OSError(errno.ENOSPC, "test")):
            with self.assertRaises(OSError):
                self.copy()
        self.client.get_object.assert_not_called()

    def test_existing_target_is_never_overwritten(self):
        self.target.write_bytes(b"do-not-overwrite")
        with self.assertRaises(FileExistsError):
            self.copy()
        self.assertEqual(self.target.read_bytes(), b"do-not-overwrite")

    def test_incomplete_download_is_rejected_and_body_closed(self):
        self.set_response(b"short", '"changed"')
        self.client.get_object.return_value["ContentLength"] = 100
        with self.assertRaisesRegex(jb.BackupError, "neúplně"):
            self.copy()
        self.client.get_object.return_value["Body"].close.assert_called_once()
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_head_missing_object_fails_backup(self):
        self.client.head_object.side_effect = self.client.exceptions.NoSuchKey
        with self.assertRaisesRegex(jb.BackupError, "zmizel"):
            self.copy()
        self.assertFalse(self.target.exists())

    def test_baseline_ignores_incomplete_corrupt_foreign_and_future_backups(self):
        make_backup(self.root, "20261024-100001", complete=False)
        corrupt = make_backup(self.root, "20261024-100002")
        (corrupt / "s3/jrc-app/objects/00000001").write_bytes(b"bad")
        make_backup(self.root, "20261024-100003", project="jrc-other")
        make_backup(self.root, "20261024-100006")
        (self.root / "20261024-100004").symlink_to(self.old, target_is_directory=True)
        self.assertEqual(jb.previous_backup(self.root, "20261024-100005"), self.old)
        shutil.rmtree(self.old)
        self.assertIsNone(jb.previous_backup(self.root, "20261024-100005"))

    def pending(self):
        stamp = "20261024-100001"
        partial = make_backup(self.root, stamp + ".partial", complete=False)
        shutil.rmtree(partial / "s3")
        (partial / "state.json").write_text(json.dumps({
            "project": self.root.name, "stamp": stamp, "postgres_version": "18.6",
        }))
        args = SimpleNamespace(workspace=str(self.work), project=self.root.name, stamp=stamp,
                               keep=7, no_env=True, media="absent", git_rev="test", git_dirty="yes")
        return partial, args

    def run_copy(self, args):
        with mock.patch.object(jb, "merged_env", return_value=fixtures.RestoreEmptinessGuards.ENV), \
                mock.patch.object(jb, "s3_client", return_value=self.client), \
                mock.patch.object(jb, "list_objects", side_effect=lambda c, b: [{"Key": "a/b.txt"}] if b == "jrc-app" else []):
            jb.cmd_copy_objects(args)

    def test_second_pass_rejects_content_and_metadata_changes(self):
        partial, args = self.pending()
        self.run_copy(args)
        for data, etag, metadata in ((b"different", '"new"', {}),
                                     (self.data, self.etag, {"Metadata": {"test": "changed"}})):
            with self.subTest(metadata=metadata):
                self.set_response(data, etag, **metadata)
                with self.assertRaisesRegex(jb.BackupError, "během zálohy změnil"):
                    self.run_copy(args)
                self.assertEqual((partial / "s3/jrc-app/objects/00000001").read_bytes(), self.data)
                self.assertEqual(self.source.read_bytes(), self.data)

    def test_new_backup_verifies_and_restores_after_deleting_old(self):
        partial, args = self.pending()
        self.run_copy(args)
        self.run_copy(args)
        new_object = partial / "s3/jrc-app/objects/00000001"
        self.assertEqual(new_object.stat().st_ino, self.source.stat().st_ino)
        self.assertEqual(new_object.stat().st_blocks, self.source.stat().st_blocks)
        self.client.get_object.assert_not_called()
        jb.cmd_finish(args)
        shutil.rmtree(self.old)
        final = self.root / args.stamp
        jb.validate_backup(final)
        with mock.patch.object(jb, "merged_env", return_value=fixtures.RestoreEmptinessGuards.ENV), \
                mock.patch.object(jb, "s3_client", return_value=self.client), \
                mock.patch.object(jb, "bucket_is_empty", return_value=True), \
                mock.patch.object(jb, "verify_restored_objects"):
            jb.cmd_restore_objects(SimpleNamespace(backup=final, project=self.root.name,
                                                  workspace=str(self.work), allow_main=False))
        self.client.upload_file.assert_called_once()
        self.assertEqual(Path(self.client.upload_file.call_args.args[0]).read_bytes(), self.data)

    def test_rotation_preserves_linked_new_backup(self):
        _, args = self.pending()
        self.run_copy(args)
        args.keep = 1
        jb.cmd_finish(args)
        self.assertFalse(self.old.exists())
        jb.validate_backup(self.root / args.stamp)

    def test_offsite_payload_still_contains_only_database_dumps_and_manifest(self):
        self.copy()
        members = []

        def encrypt(*args, stdin):
            with tarfile.open(fileobj=stdin) as archive:
                members.extend(archive.getnames())
            Path(args[args.index("--output") + 1]).write_bytes(b"encrypted-test-payload")

        with mock.patch.object(offsite, "prepare_branch"), mock.patch.object(offsite, "git"), \
                mock.patch.object(offsite, "run", side_effect=encrypt):
            offsite.make_archive(self.old, self.work / "offsite", "test-recipient", 7)
        self.assertEqual(set(members), {"manifest.json", "postgres/brightbean.dump", "postgres/freeframe.dump"})
