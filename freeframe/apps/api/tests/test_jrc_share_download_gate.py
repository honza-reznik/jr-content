"""P2-B18 R1/R2: JR Content accounts never download through share links; playback stays."""
import uuid
from unittest.mock import patch

import pytest

from apps.api.models.project import ProjectRole
from apps.api.models.share import ShareLink
from .test_jrc_live_membership import _link, live_project, isolate_request_limits  # noqa: F401
from .test_private_files import storage  # noqa: F401

SCOPES = ['asset', 'folder', 'project', 'multi']


@pytest.mark.parametrize('role', list(ProjectRole))
@pytest.mark.parametrize('scope', SCOPES)
@pytest.mark.parametrize('query', ['', '&rendition=original', '&rendition=transcode', '&prefer_original=true'])
def test_share_download_is_refused_for_every_role_and_scope(client, live_project, role, scope, query):
    ctx = live_project
    ctx.member.role = role
    ctx.db.flush()
    link = _link(ctx, scope)  # allow_download=True, as left by an older link
    root = f'/share/{link.token}'
    with patch('apps.api.routers.share.generate_presigned_get_url') as sign:
        assert client.get(f'{root}/stream/{ctx.asset.id}?download=true{query}', headers=ctx.headers).status_code == 403
        assert client.get(f'{root}/downloads/{ctx.asset.id}', headers=ctx.headers).status_code == 403
        sign.assert_not_called()


@pytest.mark.parametrize('role', list(ProjectRole))
@pytest.mark.parametrize('scope', SCOPES)
def test_share_playback_still_works(client, live_project, role, scope):
    ctx = live_project
    ctx.member.role = role
    ctx.db.flush()
    link = _link(ctx, scope)
    response = client.get(f'/share/{link.token}/stream/{ctx.asset.id}', headers=ctx.headers)
    assert response.status_code == 200
    assert '/stream/hls/' in response.json()['url']
    # The link no longer advertises downloads, so the web hides the controls.
    assert client.get(f'/share/{link.token}', headers=ctx.headers).json()['allow_download'] is False


def test_authorize_share_file_refuses_download_operation(live_project):
    from fastapi import HTTPException
    from apps.api.routers.files import authorize_share_file
    ctx = live_project
    link = _link(ctx, 'asset')
    binding = {'share_id': str(link.id), 'security': None, 'session': None, 'operation': 'download'}
    with patch('apps.api.routers.files._share_security_fingerprint', return_value=None), \
            patch('apps.api.routers.files.validate_share_link_with_session', return_value=link), \
            patch('apps.api.routers.files.validate_asset_in_share'):
        with pytest.raises(HTTPException) as refused:
            authorize_share_file(ctx.db, ctx.user, binding, ctx.asset, None)
        assert refused.value.status_code == 403
        assert 'JR Content' in refused.value.detail


@pytest.mark.parametrize('role', [ProjectRole.owner, ProjectRole.editor])
def test_new_and_edited_links_in_a_managed_project_never_allow_downloads(client, live_project, role):
    ctx = live_project
    ctx.member.role = role
    ctx.db.flush()
    created = client.post(f'/assets/{ctx.asset.id}/share', headers=ctx.headers, json={'allow_download': True})
    assert created.status_code == 201, created.text
    assert created.json()['allow_download'] is False
    token = created.json()['token']
    patched = client.patch(f'/share/{token}', headers=ctx.headers, json={'allow_download': True})
    assert patched.status_code == 200, patched.text
    assert patched.json()['allow_download'] is False
    folder = client.post(f'/folders/{ctx.folder.id}/share', headers=ctx.headers,
                         json={'allow_download': True, 'asset_ids': [str(ctx.asset.id)]})
    if folder.status_code == 201:
        assert folder.json()['allow_download'] is False
    stored = ctx.db.query(ShareLink).filter(ShareLink.token == token).one()
    assert stored.allow_download is False


def test_links_outside_managed_projects_keep_downloads(client, live_project):
    ctx = live_project
    ctx.project.jrc_managed = False
    ctx.user.jrc_managed = False
    ctx.db.flush()
    created = client.post(f'/assets/{ctx.asset.id}/share', headers=ctx.headers, json={'allow_download': True})
    assert created.status_code == 201, created.text
    assert created.json()['allow_download'] is True


@pytest.mark.parametrize('scope', SCOPES)
def test_share_of_legacy_video_without_hls_never_issues_a_raw_file(client, live_project, scope):
    ctx = live_project
    ctx.media.s3_key_processed = None
    ctx.db.flush()
    link = _link(ctx, scope)
    link.allow_download = False
    ctx.db.flush()
    with patch('apps.api.routers.share.generate_presigned_get_url') as sign:
        response = client.get(f'/share/{link.token}/stream/{ctx.asset.id}', headers=ctx.headers)
        assert response.status_code == 409
        if scope == 'asset':
            assert client.get(f'/share/{link.token}', headers=ctx.headers).json()['asset']['stream_url'] is None
        raw_calls = [c for c in sign.call_args_list if c.args and c.args[0] == ctx.media.s3_key_raw]
        assert raw_calls == []
