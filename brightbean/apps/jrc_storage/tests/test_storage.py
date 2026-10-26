# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
import os
import subprocess
import sys
from io import StringIO
from urllib.parse import urlsplit

import pytest
from botocore.exceptions import ClientError
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.jrc_storage import s3 as jrc_s3
from apps.jrc_storage.backends import JrcS3Storage
from apps.jrc_storage.management.commands.jrc_migrate_media_to_s3 import MARKER_KEY
from apps.media_library import storage as ml_storage

OPTIONS = {
    "endpoint_url": "http://jrc-s3:3900",
    "public_endpoint_url": "http://127.0.0.1:13900/",
    "access_key": "GKunittestonly",
    "secret_key": "unit-test-only",
    "bucket_name": "jrc-app",
    "region_name": "garage",
    "addressing_style": "path",
    "signature_version": "s3v4",
    "querystring_auth": True,
    "querystring_expire": 900,
}


def test_url_uses_session_gateway_with_no_signature():
    storage = JrcS3Storage(**OPTIONS)
    url = storage.url("media_library/2026/10/a b.png")
    assert url == "/files/media_library/2026/10/a%20b.png"
    assert not urlsplit(url).query
    assert storage.connection.meta.client.meta.endpoint_url == "http://jrc-s3:3900"


@override_settings(FORCE_SCRIPT_NAME="/jrc")
def test_url_respects_prefix_and_never_calls_public_presigner(monkeypatch):
    from django.urls import set_script_prefix

    storage = JrcS3Storage(**OPTIONS)
    monkeypatch.setattr(
        storage.public_connection.meta.client, "generate_presigned_url", lambda *a, **k: pytest.fail("read signature")
    )
    set_script_prefix("/jrc/")
    try:
        assert storage.url("x.png") == "/jrc/files/x.png"
    finally:
        set_script_prefix("/")


def test_unsigned_urls_are_refused():
    with pytest.raises(ImproperlyConfigured):
        JrcS3Storage(**{**OPTIONS, "querystring_auth": False})


def test_public_endpoint_is_required():
    with pytest.raises(ImproperlyConfigured), override_settings(JRC_S3_PUBLIC_URL=""):
        JrcS3Storage(**{**OPTIONS, "public_endpoint_url": ""})


def test_subclass_counts_as_s3_backend():
    with override_settings(
        STORAGES={
            "default": {"BACKEND": "apps.jrc_storage.backends.JrcS3Storage", "OPTIONS": OPTIONS},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
    ):
        assert ml_storage.is_s3_backend() is True


class FakeS3:
    """Records calls with boto3's own keyword names."""

    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.cors = None

    def head_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": self.objects[(Bucket, Key)]}

    def upload_file(self, filename, bucket, key, ExtraArgs):  # noqa: N803
        self.objects[(bucket, key)] = os.path.getsize(filename)
        self.uploads.append((key, ExtraArgs["ContentType"]))

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803
        self.objects[(Bucket, Key)] = len(Body)

    def put_bucket_cors(self, Bucket, CORSConfiguration):  # noqa: N803
        self.cors = (Bucket, CORSConfiguration)


@pytest.fixture
def fake_s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(jrc_s3, "client_and_bucket", lambda: (fake, "jrc-app"))
    for module in ("jrc_migrate_media_to_s3", "jrc_configure_s3"):
        command = __import__(f"apps.jrc_storage.management.commands.{module}", fromlist=["x"])
        monkeypatch.setattr(command, "client_and_bucket", lambda: (fake, "jrc-app"))
    return fake


def _migrate(source):
    out = StringIO()
    call_command("jrc_migrate_media_to_s3", source=str(source), stdout=out, stderr=out)
    return out.getvalue()


def _tree(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_media_copy_once_keeps_local_files_and_dot_media(tmp_path, fake_s3):
    (tmp_path / "media_library" / "2026").mkdir(parents=True)
    (tmp_path / "media_library" / "2026" / "a.png").write_bytes(b"\x89PNG" + b"0" * 100)
    (tmp_path / "avatars").mkdir()
    (tmp_path / "avatars" / "b.jpg").write_bytes(b"jpeg")
    # Platné médium může začínat tečkou; vynechá se jen provozní soubor JR Content.
    (tmp_path / "avatars" / ".portrait.png").write_bytes(b"png")
    (tmp_path / ".jrc-working-dir").write_text("/somewhere")
    (tmp_path / "link.png").symlink_to(tmp_path / "avatars" / "b.jpg")
    before = _tree(tmp_path)

    assert "copied 3, already present 0" in _migrate(tmp_path)
    assert sorted(key for key, _ in fake_s3.uploads) == [
        "avatars/.portrait.png",
        "avatars/b.jpg",
        "media_library/2026/a.png",
    ]
    assert ("jrc-app", MARKER_KEY) in fake_s3.objects
    assert _tree(tmp_path) == before

    # Po dokončení se nic nekopíruje, ani nový místní soubor.
    (tmp_path / "avatars" / "late.jpg").write_bytes(b"late")
    assert "already completed; nothing copied" in _migrate(tmp_path)
    assert len(fake_s3.uploads) == 3


def test_completed_migration_never_restores_replaced_or_deleted_media(tmp_path, fake_s3):
    (tmp_path / "avatars").mkdir()
    (tmp_path / "avatars" / "me.jpg").write_bytes(b"old local avatar")
    (tmp_path / "avatars" / "gone.jpg").write_bytes(b"deleted later")
    _migrate(tmp_path)
    fake_s3.objects[("jrc-app", "avatars/me.jpg")] = 3  # novější profilovka se stejným názvem
    del fake_s3.objects[("jrc-app", "avatars/gone.jpg")]  # smazáno v aplikaci
    _migrate(tmp_path)
    assert fake_s3.objects[("jrc-app", "avatars/me.jpg")] == 3
    assert ("jrc-app", "avatars/gone.jpg") not in fake_s3.objects


def test_existing_object_is_never_overwritten(tmp_path, fake_s3):
    (tmp_path / "avatars").mkdir()
    (tmp_path / "avatars" / "me.jpg").write_bytes(b"old local avatar")
    fake_s3.objects[("jrc-app", "avatars/me.jpg")] = 3
    output = _migrate(tmp_path)
    assert "copied 0, already present 1 (1 differing kept)" in output
    assert "not overwritten" in output
    assert fake_s3.objects[("jrc-app", "avatars/me.jpg")] == 3
    assert fake_s3.uploads == []


def test_interrupted_migration_continues(tmp_path, fake_s3, monkeypatch):
    (tmp_path / "a.png").write_bytes(b"a")
    (tmp_path / "b.png").write_bytes(b"bb")
    upload = fake_s3.upload_file

    def fail_on_b(filename, bucket, key, ExtraArgs):  # noqa: N803
        if key == "b.png":
            raise OSError("connection lost")
        upload(filename, bucket, key, ExtraArgs)

    monkeypatch.setattr(fake_s3, "upload_file", fail_on_b)
    with pytest.raises(OSError):
        _migrate(tmp_path)
    assert ("jrc-app", MARKER_KEY) not in fake_s3.objects
    monkeypatch.setattr(fake_s3, "upload_file", upload)
    assert "copied 1, already present 1" in _migrate(tmp_path)
    assert ("jrc-app", MARKER_KEY) in fake_s3.objects


@pytest.mark.skipif(os.geteuid() == 0, reason="root čte i adresář bez práv")
def test_unreadable_directory_fails_without_marker(tmp_path, fake_s3):
    locked = tmp_path / "media_library" / "locked"
    locked.mkdir(parents=True)
    (locked / "x.png").write_bytes(b"x")
    locked.chmod(0)
    try:
        with pytest.raises(CommandError, match="media_library/locked"):
            _migrate(tmp_path)
    finally:
        locked.chmod(0o755)
    assert ("jrc-app", MARKER_KEY) not in fake_s3.objects


def test_media_copy_without_local_directory(tmp_path, fake_s3):
    assert "copied 0, already present 0" in _migrate(tmp_path / "missing")
    assert fake_s3.uploads == []
    assert ("jrc-app", MARKER_KEY) in fake_s3.objects


@override_settings(APP_URL="http://127.0.0.1:13000/")
def test_bucket_cors_allows_only_the_app_origin(fake_s3):
    call_command("jrc_configure_s3", stdout=StringIO())
    bucket, config = fake_s3.cors
    assert bucket == "jrc-app"
    [rule] = config["CORSRules"]
    assert rule["AllowedOrigins"] == ["http://127.0.0.1:13000"]
    assert rule["AllowedMethods"] == ["GET", "HEAD"]


def test_jrc_settings_use_private_s3_and_exact_csp_origin():
    """Nastavení běžící aplikace: S3 backend a CSP jen s přesným původem úložiště."""
    script = """
import importlib, sys
s = importlib.import_module('config.settings.jrc')
assert s.STORAGE_BACKEND == 's3' and s.SERVE_MEDIA is False
assert s.STORAGES['default']['BACKEND'] == 'apps.jrc_storage.backends.JrcS3Storage'
assert s.AWS_S3_ENDPOINT_URL == 'http://jrc-s3:3900'
assert s.AWS_STORAGE_BUCKET_NAME == 'jrc-app'
assert s.AWS_QUERYSTRING_AUTH is True and s.AWS_QUERYSTRING_EXPIRE <= 900
assert s.CSP_REPORT_ONLY is False
origin = 'http://127.0.0.1:13999'
for name in ('CSP_IMG_SRC', 'CSP_MEDIA_SRC', 'CSP_CONNECT_SRC'):
    values = getattr(s, name)
    assert origin in values, name
    assert not any(v in ('*', 'https:', 'http:') or v.startswith('https://') for v in values), (name, values)
"""
    env = {
        **os.environ,
        "JRC_S3_ENDPOINT_URL": "http://jrc-s3:3900",
        "JRC_S3_PUBLIC_URL": "http://127.0.0.1:13999/",
        "JRC_S3_BUCKET": "jrc-app",
        "JRC_S3_ACCESS_KEY_ID": "GKunittestonly",
        "JRC_S3_SECRET_ACCESS_KEY": "unit-test-only",
        "SECRET_KEY": os.environ.get("SECRET_KEY", "unit-test-only"),
        "ENCRYPTION_KEY_SALT": os.environ.get("ENCRYPTION_KEY_SALT", "unit-test-only"),
        "DB_USER": os.environ.get("DB_USER", "jrc"),
        "DB_PASSWORD": os.environ.get("DB_PASSWORD", "unit-test-only"),
        "DB_HOST": os.environ.get("DB_HOST", "localhost"),
    }
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
