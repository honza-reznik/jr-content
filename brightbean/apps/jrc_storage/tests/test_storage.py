# JR Content: přidáno 2026-10-24 (blok F2-B05), AGPL-3.0.
import os
import subprocess
import sys
from io import StringIO
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings

from apps.jrc_storage import s3 as jrc_s3
from apps.jrc_storage.backends import JrcS3Storage
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


def test_url_is_signed_for_the_browser_facing_gate():
    storage = JrcS3Storage(**OPTIONS)
    url = storage.url("media_library/2026/10/a b.png")
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}" == "http://127.0.0.1:13900"
    assert parts.path == "/jrc-app/media_library/2026/10/a%20b.png"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["X-Amz-Signature"]
    # Server-side operations keep using the internal endpoint.
    assert storage.connection.meta.client.meta.endpoint_url == "http://jrc-s3:3900"


def test_signature_covers_the_public_host():
    storage = JrcS3Storage(**OPTIONS)
    internal = storage.connection.meta.client.generate_presigned_url(
        "get_object", Params={"Bucket": "jrc-app", "Key": "x.png"}, ExpiresIn=900
    )
    public = storage.url("x.png")
    assert parse_qs(urlsplit(internal).query)["X-Amz-Signature"] != parse_qs(urlsplit(public).query)["X-Amz-Signature"]


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


def test_media_copy_is_idempotent_and_keeps_local_files(tmp_path, fake_s3):
    (tmp_path / "media_library" / "2026").mkdir(parents=True)
    (tmp_path / "media_library" / "2026" / "a.png").write_bytes(b"\x89PNG" + b"0" * 100)
    (tmp_path / "avatars").mkdir()
    (tmp_path / "avatars" / "b.jpg").write_bytes(b"jpeg")
    (tmp_path / ".jrc-working-dir").write_text("/somewhere")
    (tmp_path / "link.png").symlink_to(tmp_path / "avatars" / "b.jpg")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    out = StringIO()
    call_command("jrc_migrate_media_to_s3", source=str(tmp_path), stdout=out)
    assert "copied 2, already present 0" in out.getvalue()
    assert sorted(fake_s3.uploads) == [("avatars/b.jpg", "image/jpeg"), ("media_library/2026/a.png", "image/png")]

    out = StringIO()
    call_command("jrc_migrate_media_to_s3", source=str(tmp_path), stdout=out)
    assert "copied 0, already present 2" in out.getvalue()
    assert len(fake_s3.uploads) == 2
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before

    # A partially copied object (different size) is copied again.
    fake_s3.objects[("jrc-app", "avatars/b.jpg")] = 1
    out = StringIO()
    call_command("jrc_migrate_media_to_s3", source=str(tmp_path), stdout=out)
    assert "copied 1, already present 1" in out.getvalue()


def test_media_copy_without_local_directory(tmp_path, fake_s3):
    out = StringIO()
    call_command("jrc_migrate_media_to_s3", source=str(tmp_path / "missing"), stdout=out)
    assert "nothing to copy" in out.getvalue()
    assert fake_s3.uploads == []


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
