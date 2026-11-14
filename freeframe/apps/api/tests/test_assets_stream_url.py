"""Regression tests for issue #51 — /assets/{id}/stream must route video HLS
through the /stream/hls proxy so S3 objects can stay private."""
import uuid
from unittest.mock import MagicMock, patch

import jwt

from apps.api.config import settings


def _setup_video_asset(mock_db, asset_type):
    from apps.api.models.asset import ProcessingStatus

    # The conftest mock_db only chains query/filter back to itself — order_by
    # creates a new auto-mock otherwise, so .first() wouldn't return our values.
    mock_db.order_by.return_value = mock_db

    asset = MagicMock()
    asset.id = uuid.uuid4()
    asset.project_id = uuid.uuid4()
    asset.asset_type = asset_type
    asset.name = "demo"
    asset.deleted_at = None

    version = MagicMock()
    version.id = uuid.uuid4()
    version.asset_id = asset.id
    version.processing_status = ProcessingStatus.ready
    version.deleted_at = None

    media_file = MagicMock()
    media_file.version_id = version.id
    media_file.s3_key_processed = "processed/proj/version-xyz"
    media_file.s3_key_raw = "raw/proj/version-xyz/input.mp4"
    media_file.original_filename = "input.mp4"

    mock_db.first.side_effect = [asset, version, media_file]
    return asset, version, media_file


@patch("apps.api.routers.assets.require_asset_access")
def test_video_stream_returns_hls_proxy_url_with_token(
    mock_require_access,
    client,
    mock_db,
    auth_headers,
):
    from apps.api.models.asset import AssetType

    asset, _, media_file = _setup_video_asset(mock_db, AssetType.video)
    mock_require_access.return_value = None

    response = client.get(f"/assets/{asset.id}/stream", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    url = body["url"]

    # The URL must route through the HLS proxy, not directly to S3.
    assert url.startswith("/stream/hls/"), (
        f"Expected a cookie-authenticated HLS URL, got: {url}"
    )
    # HLS URLs stay at the API and carry no credential in their query.
    assert "s3" not in url.split("?", 1)[0].lower(), (
        f"Stream URL must not contain a presigned S3 URL, got: {url}"
    )

    # The token must be a valid HLS JWT scoped to this asset's S3 prefix.
    assert "?" not in url
    token = response.cookies.get("ff_hls")
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == "hls"
    assert payload["pfx"] == media_file.s3_key_processed
    assert "exp" in payload


@patch("apps.api.routers.assets.generate_presigned_get_url")
@patch("apps.api.routers.assets.require_asset_access")
def test_video_download_still_returns_presigned_raw(
    mock_require_access,
    mock_presign,
    client,
    mock_db,
    auth_headers,
):
    from apps.api.models.asset import AssetType

    asset, _, _ = _setup_video_asset(mock_db, AssetType.video)
    mock_require_access.return_value = None
    mock_presign.return_value = "https://s3.example.com/raw.mp4?sig=x"

    response = client.get(
        f"/assets/{asset.id}/stream?download=true", headers=auth_headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # Downloads bypass the HLS proxy — they need the original file, not a playlist.
    assert body["url"] == "https://s3.example.com/raw.mp4?sig=x"
    assert "/stream/hls/" not in body["url"]


@patch("apps.api.routers.assets.generate_presigned_get_url")
@patch("apps.api.routers.assets.require_asset_access")
def test_image_stream_still_returns_presigned(
    mock_require_access,
    mock_presign,
    client,
    mock_db,
    auth_headers,
):
    """Images and audio don't go through the HLS proxy — only video does."""
    from apps.api.models.asset import AssetType

    asset, _, _ = _setup_video_asset(mock_db, AssetType.image)
    mock_require_access.return_value = None
    mock_presign.return_value = "https://s3.example.com/image.webp?sig=x"

    response = client.get(f"/assets/{asset.id}/stream", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == "https://s3.example.com/image.webp?sig=x"
    assert "/stream/hls/" not in body["url"]


@patch("apps.api.routers.assets.generate_presigned_get_url")
@patch("apps.api.services.downloads.head_object_size")
@patch("apps.api.routers.assets.require_asset_access")
def test_managed_video_downloads_original_with_upload_name(access, head, presign, client, mock_db, auth_headers, test_user, monkeypatch):
    from apps.api.models.asset import AssetType
    test_user.jrc_managed = False
    test_user.is_superadmin = True
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", test_user.email)
    asset, version, media = _setup_video_asset(mock_db, AssetType.video)
    media.original_filename = "Žlutý klip.MOV"
    media.s3_key_raw = "raw/project/original.mov"
    media.s3_key_download = "processed/project/download.mp4"
    head.return_value = 123
    presign.return_value = "/files/video/raw/project/original.mov?filename=clip.MOV"
    response = client.get(f"/jrc-sync/assets/{asset.id}/download-info?version_id={version.id}", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["download_note"] is None
    assert response.json()["filename"] == media.original_filename
    presign.assert_not_called()
    access.assert_called_once()


@patch("apps.api.routers.assets.generate_presigned_get_url")
@patch("apps.api.services.downloads.head_object_size")
@patch("apps.api.routers.assets.require_asset_access")
def test_managed_missing_original_falls_back_with_note(access, head, presign, client, mock_db, auth_headers, test_user, monkeypatch):
    from apps.api.models.asset import AssetType
    test_user.jrc_managed = False
    test_user.is_superadmin = True
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", test_user.email)
    asset, version, media = _setup_video_asset(mock_db, AssetType.video)
    media.s3_key_download = "processed/project/download.mp4"
    head.side_effect = [None, 456]
    presign.return_value = "/files/video/processed/project/download.mp4?filename=demo.mp4"
    response = client.get(f"/jrc-sync/assets/{asset.id}/download-info?version_id={version.id}", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert "original upload is unavailable" in response.json()["download_note"]
    assert response.json()["filename"].endswith(".mp4")
    presign.assert_not_called()


@patch("apps.api.services.downloads.head_object_size", return_value=None)
@patch("apps.api.routers.assets.require_asset_access")
def test_managed_missing_all_downloads_returns_404(access, head, client, mock_db, auth_headers, test_user, monkeypatch):
    from apps.api.models.asset import AssetType
    test_user.jrc_managed = False
    test_user.is_superadmin = True
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", test_user.email)
    asset, version, media = _setup_video_asset(mock_db, AssetType.video)
    media.s3_key_download = "processed/project/download.mp4"
    response = client.get(f"/jrc-sync/assets/{asset.id}/download-info?version_id={version.id}", headers=auth_headers)
    assert response.status_code == 404


@patch("apps.api.services.downloads.head_object_size", side_effect=RuntimeError("storage unavailable"))
@patch("apps.api.routers.assets.require_asset_access")
def test_managed_storage_failure_does_not_silently_fallback(access, head, client, mock_db, auth_headers, test_user, monkeypatch):
    from apps.api.models.asset import AssetType
    test_user.jrc_managed = False
    test_user.is_superadmin = True
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", test_user.email)
    asset, version, _ = _setup_video_asset(mock_db, AssetType.video)
    response = client.get(f"/jrc-sync/assets/{asset.id}/download-info?version_id={version.id}", headers=auth_headers)
    assert response.status_code == 500
