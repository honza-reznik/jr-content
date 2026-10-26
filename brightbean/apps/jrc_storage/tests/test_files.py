"""P1-B03: the same address is checked again for every viewer and request."""

from io import BytesIO
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

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
    stranger = get_user_model().objects.create_user(email="stranger@example.com", password="testpass123")
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
