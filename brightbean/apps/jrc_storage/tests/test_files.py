"""P1-B03: the same address is checked again for every viewer and request."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.approvals.models import PostComment
from apps.composer.models import Post
from apps.jrc_storage import views
from apps.jrc_storage.limits import TransferUnavailableError
from apps.media_library.models import MediaAsset, MediaAssetVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def files(user, organization):
    workspace = Workspace.objects.create(organization=organization, name="Private files", icon="workspaces/icons/a.png")
    OrgMembership.objects.create(user=user, organization=organization)
    membership = WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="editor")
    user.avatar = "avatars/me.png"
    user.save(update_fields=["avatar"])
    asset = MediaAsset.objects.create(
        organization=organization,
        workspace=workspace,
        uploaded_by=user,
        file="media_library/a.mp4",
        thumbnail="media_library/thumbs/a.png",
        filename="a.mp4",
    )
    MediaAssetVersion.objects.create(
        media_asset=asset, version_number=1, file="media_library/versions/a.mp4", thumbnail="media_library/thumbs/v.png"
    )
    post = Post.objects.create(workspace=workspace, author=user)
    external = PostComment.objects.create(
        post=post, author=user, body="external", visibility="external", attachment="comment_attachments/external.png"
    )
    internal = PostComment.objects.create(
        post=post, author=user, body="internal", visibility="internal", attachment="comment_attachments/internal.png"
    )
    return membership, external, internal


def address(key):
    return reverse("jrc_private_file", kwargs={"path": key})


@pytest.fixture
def storage(monkeypatch):
    release = Mock()
    monkeypatch.setattr(views, "acquire", lambda user: release)
    client = Mock()
    client.head_object.return_value = {"ContentLength": 10}
    bodies = []

    def get(**params):
        data = b"0123456789"
        if "Range" in params:
            first, last = params["Range"][6:].split("-")
            data = data[int(first) : int(last) + 1]
        body = BytesIO(data)
        bodies.append(body)
        return {"Body": body}

    client.get_object.side_effect = get
    monkeypatch.setattr(views, "_storage", lambda: (client, "jrc-app"))
    return client, bodies, release


@pytest.mark.parametrize(
    "key",
    [
        "media_library/a.mp4",
        "media_library/thumbs/a.png",
        "media_library/versions/a.mp4",
        "media_library/thumbs/v.png",
        "avatars/me.png",
        "workspaces/icons/a.png",
        "comment_attachments/external.png",
        "comment_attachments/internal.png",
    ],
)
def test_all_file_types_require_live_membership_and_session(client, user, files, storage, key):
    url = address(key)
    assert client.get(url).status_code == 403
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"0123456789"
    assert response["Cache-Control"] == "private, no-store"
    stranger = get_user_model().objects.create_user(
        email="stranger@example.com", password="testpass123", tos_accepted_at=timezone.now()
    )
    client.force_login(stranger)
    assert client.get(url).status_code == 404
    client.logout()
    assert client.get(url).status_code == 403


@pytest.mark.parametrize("key", ["comment_attachments/internal.png", "comment_attachments/reply.png"])
def test_client_cannot_read_internal_thread(client, user, files, storage, key):
    membership, external, internal = files
    PostComment.objects.create(
        post=internal.post,
        author=user,
        parent_comment=internal,
        body="reply",
        visibility="external",
        attachment="comment_attachments/reply.png",
    )
    membership.workspace_role = "client"
    membership.save()
    client.force_login(user)
    assert client.get(address(key)).status_code == 404
    assert client.get(address(external.attachment.name)).status_code == 200


def test_revoked_membership_and_deleted_attachment(client, user, files, storage):
    membership, external, _ = files
    client.force_login(user)
    external.deleted_at = __import__("django.utils.timezone", fromlist=["now"]).now()
    external.save()
    assert client.get(address(external.attachment.name)).status_code == 404
    membership.delete()
    assert client.get(address("media_library/a.mp4")).status_code == 404


@pytest.mark.parametrize(
    ("header", "expected", "payload"),
    [("bytes=2-5", "bytes 2-5/10", b"2345"), ("bytes=-3", "bytes 7-9/10", b"789"), ("bytes=8-", "bytes 8-9/10", b"89")],
)
def test_ranges_are_read_from_storage_in_bounded_chunks(client, user, files, storage, header, expected, payload):
    client.force_login(user)
    response = client.get(address("media_library/a.mp4"), HTTP_RANGE=header)
    assert response.status_code == 206
    assert response["Content-Range"] == expected
    assert b"".join(response.streaming_content) == payload
    response.close()
    assert storage[1][0].closed
    assert storage[2].called


@pytest.mark.parametrize("header", ["bytes=99-", "bytes=-0", "bytes=0-1,3-4", "bytes=", "other"])
def test_invalid_range_releases_capacity_without_opening_storage_body(client, user, files, storage, header):
    client.force_login(user)
    response = client.get(address("media_library/a.mp4"), HTTP_RANGE=header)
    assert response.status_code == 416
    assert response["Content-Range"] == "bytes */10"
    storage[0].get_object.assert_not_called()
    storage[2].assert_called_once()


def test_close_before_iteration_releases_storage_and_budget(client, user, files, storage):
    client.force_login(user)
    response = client.get(address("media_library/a.mp4"))
    response.close()
    assert storage[1][0].closed
    storage[2].assert_called_once()


@pytest.mark.parametrize("status", [429, 503])
def test_file_admission_fails_closed(client, user, files, monkeypatch, status):
    client.force_login(user)

    def deny(user):
        raise TransferUnavailableError(status)

    monkeypatch.setattr(views, "acquire", deny)
    assert client.get(address("media_library/a.mp4")).status_code == status


def test_archived_workspace_and_deleted_organization_deny_files(client, user, files, storage):
    membership, _, _ = files
    client.force_login(user)
    workspace = membership.workspace
    workspace.is_archived = True
    workspace.save()
    assert client.get(address("media_library/a.mp4")).status_code == 404
    assert client.get(address("comment_attachments/external.png")).status_code == 404
    workspace.is_archived = False
    workspace.save()
    workspace.organization.deleted_at = timezone.now()
    workspace.organization.save()
    assert client.get(address("media_library/a.mp4")).status_code == 404
    assert client.get(address("comment_attachments/external.png")).status_code == 404


def test_head_returns_length_and_releases_capacity(client, user, files, storage):
    client.force_login(user)
    response = client.head(address("media_library/a.mp4"))
    assert response.status_code == 200
    assert response["Content-Length"] == "10"
    storage[0].get_object.assert_not_called()
    storage[2].assert_called_once()


def test_redis_identity_matches_freeframe_and_leases_release_independently(user, monkeypatch):
    import hashlib
    import uuid

    from apps.jrc_storage import limits

    redis = Mock()
    redis.eval.return_value = 1
    monkeypatch.setattr(limits, "connection", lambda: redis)
    release = limits.acquire(user)
    identity = uuid.uuid5(uuid.UUID("29c4ee9e-ab19-48c4-bb44-2fc77109b234"), f"account:{user.pk}")
    digest = hashlib.sha256(f"user:{identity}".encode()).hexdigest()
    args = redis.eval.call_args.args
    assert args[2:6] == ("hls:active:total", f"hls:active:{digest}", "hls:rate:total", f"hls:rate:{digest}")
    assert args[6:10] == (32, 4, 6000, 240)
    release()
    assert redis.eval.call_args.args == (limits.RELEASE, 2, *args[2:4], args[-1])


def test_video_without_remote_account_is_hidden(client, user):
    client.force_login(user)
    assert client.get(address("video/raw/file.mp4")).status_code == 404


def test_video_gateway_retries_expired_internal_session_and_never_forwards_cookies(client, user, monkeypatch):
    import uuid

    from apps.jrc_video.models import VideoAccount

    VideoAccount.objects.create(user=user, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    api = Mock(base_url="http://jrc-ff-api:8000", token="internal-only")
    monkeypatch.setattr(views, "FreeFrameUserClient", lambda user: api)
    expired = Mock(status_code=401)
    upstream = Mock(status_code=206, headers={"Content-Type": "video/mp4", "Content-Range": "bytes 2-5/10"})
    upstream.raw = BytesIO(b"2345")
    send = Mock(side_effect=[expired, upstream])
    monkeypatch.setattr(views.requests, "request", send)
    client.force_login(user)
    response = client.get(
        address("video/raw/a b.mp4"), {"filename": "Final.mp4", "token": "ignored"}, HTTP_RANGE="bytes=2-5"
    )
    assert response.status_code == 206
    assert b"".join(response.streaming_content) == b"2345"
    assert send.call_args.args[1] == "http://jrc-ff-api:8000/files/raw/a%20b.mp4"
    assert send.call_args.kwargs["headers"] == {"Authorization": "Bearer internal-only", "Range": "bytes=2-5"}
    assert send.call_args.kwargs["params"] == {"filename": "Final.mp4"}
    assert send.call_args.kwargs["allow_redirects"] is False
    assert [call.kwargs["renew"] for call in api._authenticate.call_args_list] == [False, True]
    expired.close.assert_called_once()
    response.close()
    assert upstream.close.called


@pytest.mark.parametrize("status", [403, 404, 410, 416, 429, 503])
def test_video_error_does_not_forward_length_of_discarded_body(client, user, monkeypatch, status):
    import uuid

    from apps.jrc_video.models import VideoAccount

    VideoAccount.objects.create(user=user, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    monkeypatch.setattr(views, "FreeFrameUserClient", lambda user: Mock(base_url="http://jrc-ff-api:8000"))
    upstream = Mock(status_code=status, headers={"content-length": "57", "Retry-After": "60"})
    monkeypatch.setattr(views.requests, "request", lambda *a, **k: upstream)
    client.force_login(user)
    response = client.get(address("video/raw/file.mp4"))
    assert response.status_code == status
    assert response.content == b""
    assert response.get("Content-Length", "0") == "0"
    assert response["Retry-After"] == "60"
    upstream.close.assert_called_once()
