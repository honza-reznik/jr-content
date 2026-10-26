from django.test import override_settings
from django.urls import reverse

import pytest

pytestmark = pytest.mark.django_db


def test_source_archive_requires_login(client, tmp_path):
    with override_settings(JRC_SOURCE_VERSION="abc1234", JRC_SOURCE_ARCHIVE_DIR=tmp_path):
        response = client.get(reverse("jrc_ui:source_code"))
    assert response.status_code == 302
    assert reverse("jrc_ui:source_code") in response.url


def test_source_archive_downloads_named_build_archive(client, user, tmp_path):
    archive = tmp_path / "jr-content-abc1234.tar.gz"
    archive.write_bytes(b"test archive")
    client.force_login(user)
    with override_settings(JRC_SOURCE_VERSION="abc1234", JRC_SOURCE_ARCHIVE_DIR=tmp_path):
        response = client.get(reverse("jrc_ui:source_code"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/gzip"
    assert response["Content-Disposition"] == 'attachment; filename="jr-content-abc1234.tar.gz"'
    assert b"".join(response.streaming_content) == b"test archive"


def test_source_archive_rejects_unexpected_version_path(client, user, tmp_path):
    client.force_login(user)
    with override_settings(JRC_SOURCE_VERSION="../../.env", JRC_SOURCE_ARCHIVE_DIR=tmp_path):
        assert client.get(reverse("jrc_ui:source_code")).status_code == 404
