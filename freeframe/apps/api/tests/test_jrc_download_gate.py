"""P2-B18: managed roles never download through member APIs, including bulk and exact keys."""
import uuid
from unittest.mock import patch

import pytest

from apps.api.models.project import ProjectRole
from .test_jrc_live_membership import live_project, isolate_request_limits  # noqa: F401
from .test_private_files import storage  # noqa: F401


@pytest.mark.parametrize('role', ['owner', 'manager', 'editor', 'client', 'viewer'])
@pytest.mark.parametrize('query', ['', '&rendition=original', '&rendition=transcode', '&prefer_original=true'])
@pytest.mark.parametrize('embed', ['', '&embed=1'])
def test_managed_roles_cannot_mint_download_urls(client, auth_headers, test_user, role, query, embed):
    test_user.jrc_managed = True
    with patch('apps.api.routers.assets._ready_media_file') as ready:
        response = client.get(f'/assets/{uuid.uuid4()}/stream?download=true{query}{embed}', headers=auth_headers)
        assert response.status_code == 403
        ready.assert_not_called()  # No file or storage side effects before denial.
    assert client.get(f'/assets/{uuid.uuid4()}/downloads', headers=auth_headers).status_code == 403


@pytest.mark.parametrize('role', list(ProjectRole))
@pytest.mark.parametrize('kind', ['raw', 'download', 'processed'])
@pytest.mark.parametrize('method', ['get', 'head'])
def test_exact_keys_are_denied_with_real_membership(client, live_project, storage, role, kind, method):
    ctx = live_project
    ctx.member.role = role
    ctx.db.flush()
    ctx.media.s3_key_download = "processed/converted.mp4"
    ctx.db.flush()
    key = getattr(ctx.media, f's3_key_{kind}')
    assert key
    for suffix in ['', '?filename=clip.mp4', '?download=false&embed=1']:
        assert getattr(client, method)('/files/' + key + suffix, headers=ctx.headers).status_code == 403
    storage[0].get_object.assert_not_called()


@pytest.mark.parametrize('managed,admin', [(True, False), (True, True), (False, False), (False, True)])
@pytest.mark.parametrize('path', ['download-info', 'download'])
def test_only_named_service_account_can_use_internal_downloads(client, auth_headers, test_user, monkeypatch, managed, admin, path):
    test_user.jrc_managed = managed
    test_user.is_superadmin = admin
    monkeypatch.setenv('JRC_FF_SYNC_EMAIL', 'another@example.invalid')
    response = client.get(f'/jrc-sync/assets/{uuid.uuid4()}/{path}?version_id={uuid.uuid4()}&actor_id={uuid.uuid4()}', headers=auth_headers)
    assert response.status_code == 403


def test_service_download_streams_selected_version_with_real_actor_budget(client, auth_headers, test_user, monkeypatch, storage):
    from fastapi import HTTPException
    from apps.api.models.asset import AssetType
    from apps.api.routers import files
    from unittest.mock import Mock
    # Membership/version resolution remains in the normal access checker; isolate its I/O here.
    media = Mock(s3_key_raw='raw/original.mov', original_filename='Žlutý klip.MOV', s3_key_download='processed/download.mp4', s3_key_processed='processed/hls')
    asset = Mock(asset_type=AssetType.video)
    test_user.is_superadmin = True
    monkeypatch.setenv('JRC_FF_SYNC_EMAIL', test_user.email)
    actor, version, asset_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    acquire = Mock(return_value=storage[2])
    monkeypatch.setattr(files.hls_limits, 'acquire', acquire)
    with patch('apps.api.routers.assets._ready_media_file', return_value=(asset, media)) as ready, patch('apps.api.services.downloads.head_object_size', return_value=10):
        response = client.get(f'/jrc-sync/assets/{asset_id}/download?version_id={version}&actor_id={actor}', headers=dict(auth_headers, Range='bytes=2-5'))
        assert response.status_code == 206
        assert response.content == b'2345'
        assert "filename*=UTF-8''" in response.headers['content-disposition']
        assert ready.call_args.args[1:3] == (asset_id, version)
        acquire.assert_called_once_with(f'user:{actor}')
    storage[2].assert_called()
    with patch('apps.api.routers.assets._ready_media_file', side_effect=HTTPException(404, 'Version not found')):
        assert client.get(f'/jrc-sync/assets/{asset_id}/download-info?version_id={version}', headers=auth_headers).status_code == 404


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_nonvideo_inline_playback_still_works(client, live_project, storage, kind):
    from apps.api.models.asset import AssetType
    ctx = live_project
    ctx.asset.asset_type = AssetType(kind)
    ctx.db.flush()
    response = client.get('/files/' + ctx.media.s3_key_raw, headers=ctx.headers)
    assert response.status_code == 200
    assert response.content == b'0123456789'


def test_managed_video_without_hls_never_falls_back_to_a_raw_read_url(client, auth_headers, test_user):
    from unittest.mock import Mock
    from apps.api.models.asset import AssetType
    test_user.jrc_managed = True
    asset = Mock(asset_type=AssetType.video)
    media = Mock(s3_key_processed=None, s3_key_raw='raw/old-original.mov')
    with patch('apps.api.routers.assets._ready_media_file', return_value=(asset, media)), patch('apps.api.routers.assets.generate_presigned_get_url', return_value='https://storage.example.invalid/raw.mov?signature=example') as sign:
        response = client.get(f'/assets/{uuid.uuid4()}/stream?download=false', headers=auth_headers)
        assert response.status_code == 409
        sign.assert_not_called()
