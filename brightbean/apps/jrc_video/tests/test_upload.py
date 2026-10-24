"""F5-B05: upload of a channel video version through FreeFrame on behalf of the user.

FreeFrame itself is replaced by a recording fake: these tests check what Brightbean asks for,
for whom and what it refuses before asking. The live check with a real video is
``scripts/F5-B05/check-upload.cjs``.
"""

import json
import uuid

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_video import uploads
from apps.jrc_video.client import FreeFrameError
from apps.jrc_video.models import VideoAccount, VideoAsset, VideoProject, VideoVersion
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db
S3 = "http://127.0.0.1:14053"


class FakeFreeFrame:
    """Records every call; answers like the FreeFrame upload API."""

    def __init__(self):
        self.calls = []
        self.versions = {}  # asset id -> list of dict
        self.fail = {}  # (method, path prefix) -> status
        self.expires = 900
        self.url_origin = S3
        self.held = [1]
        self.state = "resumable"
        self.abort_keeps_version = False

    def __call__(self, user):
        fake = self

        class Client:
            def request(self, method, path, payload=None):
                fake.calls.append((user.pk, method, path, payload))
                for (m, prefix), status in fake.fail.items():
                    if m == method and path.startswith(prefix):
                        raise FreeFrameError("fail", status_code=status)
                return fake.answer(method, path, payload)

        return Client()

    def answer(self, method, path, payload):
        if path == "/upload/initiate":
            asset = str(uuid.uuid4())
            return self._new_version(asset, payload, project=payload["project_id"])
        if path.startswith("/assets/") and path.endswith("/versions") and method == "POST":
            asset = path.split("/")[2]
            return self._new_version(asset, payload, project=payload["project_id"])
        if path.startswith("/assets/") and path.endswith("/versions") and method == "GET":
            return self.versions.get(path.split("/")[2], [])
        if path == "/upload/abort":
            version = payload["version_id"]
            asset = self.asset_of[version]
            if self.abort_keeps_version:
                for item in self.versions[asset]:
                    if item["id"] == version:
                        item["processing_status"] = "processing"
            else:
                self.versions[asset] = [item for item in self.versions[asset] if item["id"] != version]
            return None  # FreeFrame's endpoint answers 204.
        if path.startswith("/upload/") and path.endswith("/parts"):
            version = path.split("/")[2]
            return {
                "state": self.state,
                "upload_id": "upload-1",
                "s3_key": self.key_of[version],
                "asset_id": self.asset_of[version],
                "version_id": version,
                "chunk_size_bytes": 10 * 1024 * 1024,
                "file_size_bytes": self.size_of[version],
                "original_filename": "x.mp4",
                "mime_type": "video/mp4",
                "held_part_numbers": self.held,
            }
        if path == "/upload/presign-part":
            return {
                "presigned_url": f"{self.url_origin}/jrc-video/{payload['s3_key']}?partNumber={payload['part_number']}"
                f"&X-Amz-Expires={self.expires}",
                "part_number": payload["part_number"],
            }
        return {"status": "processing"}

    key_of = {}
    asset_of = {}
    size_of = {}

    def _new_version(self, asset, payload, project):
        version = str(uuid.uuid4())
        entries = self.versions.setdefault(asset, [])
        number = len(entries) + 1
        entries.append({"id": version, "version_number": number, "processing_status": "uploading", "files": []})
        key = f"raw/{project}/{asset}/{version}/original.mp4"
        self.key_of = {**self.key_of, version: key}
        self.asset_of = {**self.asset_of, version: asset}
        self.size_of = {**self.size_of, version: payload["file_size_bytes"]}
        return {
            "upload_id": "upload-1",
            "s3_key": key,
            "asset_id": asset,
            "version_id": version,
            "chunk_size_bytes": 10 * 1024 * 1024,
        }

    def paths(self, method=None):
        return [path for _u, m, path, _p in self.calls if method in (None, m)]


@pytest.fixture
def ff(monkeypatch):
    fake = FakeFreeFrame()
    monkeypatch.setattr(uploads, "FreeFrameUserClient", fake)
    return fake


def make_user(workspace, email, role):
    person = User.objects.create_user(email=email, password="x", name=email, tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
    VideoAccount.objects.create(user=person, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    return person


@pytest.fixture(autouse=True)
def _channels_on():
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False, JRC_S3_PUBLIC_ORIGIN=S3):
        yield


@pytest.fixture
def workspace(organization, user):
    workspace = Workspace.objects.create(organization=organization, name="Video test")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    return workspace


@pytest.fixture
def setup(workspace, user):
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(workspace_role="manager")
    VideoAccount.objects.create(user=user, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    VideoProject.objects.create(workspace=workspace, ff_project_id=uuid.uuid4(), synced_at=timezone.now())
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Ella garden reel", user)
    return post


def url(name, workspace, post, *args):
    return reverse(f"jrc_video:{name}", kwargs={"workspace_id": workspace.id, "post_id": post.id, **dict(args)})


def post_json(client, address, data):
    return client.post(address, json.dumps(data), content_type="application/json")


def channel_key(post):
    return str(post.platform_posts.first().pk)


def start(client, workspace, post, **overrides):
    data = {"channel": channel_key(post), "filename": "reel.mp4", "size": 25_000_000, "type": "video/mp4"}
    data.update(overrides)
    return post_json(client, url("upload_start", workspace, post), data)


# --- start ---------------------------------------------------------------------------


def test_first_upload_creates_asset_and_version_in_the_users_name(client, ff, workspace, user, setup):
    client.force_login(user)
    response = start(client, workspace, setup)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["chunk_size"] == 10 * 1024 * 1024 and body["held_parts"] == [] and body["number"] == 1
    # Every FreeFrame call is made with the requesting user's client.
    assert {call[0] for call in ff.calls} == {user.pk}
    assert ff.paths("POST")[0] == "/upload/initiate"
    initiate = ff.calls[0][3]
    assert initiate["project_id"] == str(VideoProject.objects.get().ff_project_id)
    assert initiate["mime_type"] == "video/mp4" and initiate["file_size_bytes"] == 25_000_000
    video = VideoAsset.objects.get()
    version = VideoVersion.objects.get()
    assert video.platform_post.post == setup and version.video == video
    assert version.status == "uploading" and version.uploaded_by == user and version.number == 1
    assert str(video.ff_asset_id) in body["s3_key"] and str(version.ff_version_id) in body["s3_key"]


def test_second_version_uses_the_same_asset_and_freeframes_number(client, ff, workspace, user, setup):
    client.force_login(user)
    first = start(client, workspace, setup).json()
    version = VideoVersion.objects.get()
    VideoVersion.objects.filter(pk=version.pk).update(status="ready")
    # FreeFrame skips a number after a discarded upload; we copy its numbering.
    ff.versions[str(version.video.ff_asset_id)].append(
        {"id": str(uuid.uuid4()), "version_number": 2, "processing_status": "failed", "files": []}
    )
    second = start(client, workspace, setup)
    assert second.status_code == 200, second.content
    assert second.json()["number"] == 3 and first["number"] == 1
    assert ff.paths("POST")[-1] == f"/assets/{version.video.ff_asset_id}/versions"
    assert VideoAsset.objects.count() == 1


@pytest.mark.parametrize("role", ["client", "viewer"])
def test_client_and_viewer_cannot_start_even_with_a_direct_request(client, ff, workspace, setup, role):
    person = make_user(workspace, f"{role}@example.com", role)
    client.force_login(person)
    response = start(client, workspace, setup)
    assert response.status_code == 403
    assert ff.calls == [] and not VideoVersion.objects.exists()


def test_contributor_only_for_own_content(client, ff, workspace, user, setup):
    contributor = make_user(workspace, "contrib@example.com", "contributor")
    client.force_login(contributor)
    assert start(client, workspace, setup).status_code == 403  # content of another author
    own = create_content(workspace, "Own reel", contributor)
    response = post_json(
        client,
        url("upload_start", workspace, own),
        {"channel": channel_key(own), "filename": "a.mp4", "size": 10, "type": ""},
    )
    assert response.status_code == 200, response.content


def test_custom_role_never_uploads(client, ff, workspace, setup):
    from apps.members.models import CustomRole

    person = make_user(workspace, "custom@example.com", "editor")
    role = CustomRole.objects.create(
        organization=workspace.organization,
        name="Special",
        permissions={"create_posts": True, "edit_others_posts": True},
    )
    WorkspaceMembership.objects.filter(user=person).update(custom_role=role)
    client.force_login(person)
    assert start(client, workspace, setup).status_code == 403
    assert ff.calls == []


def test_stranger_and_anonymous(client, ff, workspace, setup):
    assert start(client, workspace, setup).status_code == 302  # login required
    stranger = User.objects.create_user(email="s@example.com", password="x", tos_accepted_at=timezone.now())
    client.force_login(stranger)
    assert start(client, workspace, setup).status_code == 403
    assert ff.calls == []


def test_requires_csrf_token(workspace, user, setup, ff):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    assert start(strict, workspace, setup).status_code == 403
    assert ff.calls == []


def test_archived_workspace_is_refused(client, ff, workspace, user, setup):
    Workspace.objects.filter(pk=workspace.pk).update(is_archived=True)
    client.force_login(user)
    assert start(client, workspace, setup).status_code == 403
    assert ff.calls == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"filename": "x.exe"}, "Unsupported"),
        ({"filename": "x"}, "Unsupported"),
        ({"filename": ""}, "valid name"),
        ({"filename": "a" * 300 + ".mp4"}, "valid name"),
        ({"size": 0}, "empty"),
        ({"size": -5}, "empty"),
        ({"size": "10"}, "empty"),
        ({"size": True}, "empty"),
        ({"size": 5 * 1024**3}, "larger"),
        ({"type": "application/zip"}, "video file"),
    ],
)
def test_size_and_type_are_limited_before_freeframe_is_asked(client, ff, workspace, user, setup, overrides, fragment):
    client.force_login(user)
    response = start(client, workspace, setup, **overrides)
    assert response.status_code == 400 and fragment in response.json()["error"]
    assert ff.calls == [] and not VideoVersion.objects.exists()


def test_extension_decides_the_type_and_path_parts_are_dropped(client, ff, workspace, user, setup):
    client.force_login(user)
    response = start(client, workspace, setup, filename="..\\..\\evil/Clip.MOV", type="")
    assert response.status_code == 200, response.content
    body = ff.calls[0][3]
    assert body["original_filename"] == "Clip.MOV" and body["mime_type"] == "video/quicktime"


@override_settings(JRC_VIDEO_MAX_UPLOAD_BYTES=1000)
def test_configurable_size_limit(client, ff, workspace, user, setup):
    client.force_login(user)
    assert start(client, workspace, setup, size=1001).status_code == 400
    assert start(client, workspace, setup, size=1000).status_code == 200


def test_one_unfinished_upload_per_user_and_channel(client, ff, workspace, user, setup):
    client.force_login(user)
    assert start(client, workspace, setup).status_code == 200
    again = start(client, workspace, setup)
    assert again.status_code == 409
    assert VideoVersion.objects.count() == 1


def test_foreign_channel_is_not_found(client, ff, workspace, user, setup, organization):
    other = Workspace.objects.create(organization=organization, name="Other client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    create_manual_channel(other, "instagram", "Other")
    foreign = create_content(other, "Foreign", user)
    client.force_login(user)
    response = start(client, workspace, setup, channel=channel_key(foreign))
    assert response.status_code == 404 and ff.calls == []


def test_freeframe_refusal_is_a_plain_message_without_internals(client, ff, workspace, user, setup):
    ff.fail[("POST", "/upload/initiate")] = 403
    client.force_login(user)
    response = start(client, workspace, setup)
    assert response.status_code == 403
    assert "not allowed" in response.json()["error"]
    assert not VideoAsset.objects.exists() and not VideoVersion.objects.exists()
    ff.fail[("POST", "/upload/initiate")] = 500
    assert start(client, workspace, setup).status_code == 503


def test_not_synchronized_client_has_clear_error(client, ff, workspace, user, setup):
    VideoProject.objects.all().delete()
    client.force_login(user)
    response = start(client, workspace, setup)
    assert response.status_code == 503 and "not ready" in response.json()["error"]
    assert ff.calls == []


def test_failed_local_start_compensates_the_remote_upload(client, ff, workspace, user, setup, monkeypatch):
    client.force_login(user)

    def fail_number(*_args):
        raise uploads.UploadError("forged incomplete version listing", 502)

    monkeypatch.setattr(uploads, "_remote_number", fail_number)
    response = start(client, workspace, setup)
    assert response.status_code == 502
    aborts = [call for call in ff.calls if call[2] == "/upload/abort"]
    assert len(aborts) == 1 and aborts[0][3]["discard"] is True
    assert not VideoAsset.objects.exists() and not VideoVersion.objects.exists()
    assert ff.versions and all(not versions for versions in ff.versions.values())


def test_failed_compensation_keeps_a_local_mirror_of_the_remote_upload(client, ff, workspace, user, setup, monkeypatch):
    client.force_login(user)

    def fail_number(*_args):
        raise uploads.UploadError("forged unavailable listing", 502)

    monkeypatch.setattr(uploads, "_remote_number", fail_number)
    ff.fail[("POST", "/upload/abort")] = 503

    response = start(client, workspace, setup)
    assert response.status_code == 200, response.content
    version = VideoVersion.objects.get()
    assert response.json()["version"] == str(version.pk)
    assert version.status == "uploading" and version.number == 1
    assert str(version.ff_version_id) in [item["id"] for item in ff.versions[str(version.video.ff_asset_id)]]
    ff.versions[str(version.video.ff_asset_id)][0]["version_number"] = 4
    client.get(url("upload_status", workspace, setup))
    version.refresh_from_db()
    assert version.number == 4  # Status reconciliation also replaces the provisional number.


def test_enabled_channel_without_copy_gets_one(client, ff, workspace, user, setup):
    from apps.social_accounts.models import SocialAccount

    create_manual_channel(workspace, "instagram", "Late channel")
    account = SocialAccount.objects.get(account_name="Late channel")
    client.force_login(user)
    response = post_json(
        client,
        url("upload_start", workspace, setup),
        {"channel": f"new:{account.pk}", "filename": "a.mp4", "size": 10, "type": "video/mp4"},
    )
    assert response.status_code == 200, response.content
    assert PlatformPost.objects.filter(post=setup, social_account=account).count() == 1


# --- parts, resume, complete ---------------------------------------------------------


def started(client, workspace, post):
    return start(client, workspace, post).json()


def part(client, workspace, post, info, number=1, **overrides):
    data = {"part": number, "s3_key": info["s3_key"], "upload_id": info["upload_id"]}
    data.update(overrides)
    return post_json(client, url("upload_part", workspace, post, ("version_id", info["version"])), data)


def test_part_address_is_for_the_uploader_only_and_short(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    response = part(client, workspace, setup, info, 3)
    assert response.status_code == 200, response.content
    assert response.json()["url"].startswith(S3) and "partNumber=3" in response.json()["url"]
    assert ff.calls[-1][0] == user.pk and ff.calls[-1][2] == "/upload/presign-part"
    # Somebody else (even an editor of the same client) cannot get addresses for it.
    other = make_user(workspace, "editor2@example.com", "editor")
    client.force_login(other)
    assert part(client, workspace, setup, info).status_code == 404
    assert [c for c in ff.calls if c[0] == other.pk] == []


def test_part_rejects_foreign_key_bad_number_and_long_lived_or_foreign_url(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    before = len(ff.calls)
    foreign_key = info["s3_key"].replace(info["s3_key"].split("/")[3], str(uuid.uuid4()))
    assert part(client, workspace, setup, info, s3_key=foreign_key).status_code == 403
    assert part(client, workspace, setup, info, s3_key="../../x").status_code == 403
    assert part(client, workspace, setup, info, upload_id="").status_code == 403
    for bad in (0, 10001, "1", True, None):
        assert part(client, workspace, setup, info, number=bad).status_code == 400
    assert len(ff.calls) == before
    ff.expires = 3600
    assert part(client, workspace, setup, info).status_code == 502
    ff.expires = 900
    ff.url_origin = "http://evil.example"
    assert part(client, workspace, setup, info).status_code == 502


def test_part_refused_after_the_user_lost_the_editor_role(client, ff, workspace, user, setup):
    person = make_user(workspace, "ed@example.com", "editor")
    own = create_content(workspace, "Ed reel", person)
    client.force_login(person)
    info = post_json(
        client,
        url("upload_start", workspace, own),
        {"channel": channel_key(own), "filename": "a.mp4", "size": 10, "type": ""},
    ).json()
    WorkspaceMembership.objects.filter(user=person).update(workspace_role="viewer")
    calls = len(ff.calls)
    response = part(client, workspace, own, info)
    assert response.status_code == 403 and len(ff.calls) == calls


def test_resume_returns_held_parts_and_complete_marks_processing(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    address = url("upload_resume", workspace, setup, ("version_id", info["version"]))
    resumed = client.get(address)
    assert resumed.status_code == 200, resumed.content
    assert resumed.json()["held_parts"] == [1] and resumed.json()["state"] == "resumable"
    assert resumed.json()["s3_key"] == info["s3_key"]
    done = post_json(
        client,
        url("upload_complete", workspace, setup, ("version_id", info["version"])),
        {"s3_key": info["s3_key"], "upload_id": info["upload_id"]},
    )
    assert done.status_code == 200, done.content
    assert VideoVersion.objects.get().status == "processing"
    complete = [c for c in ff.calls if c[2] == "/upload/complete"][0][3]
    assert complete["version_id"] == str(VideoVersion.objects.get().ff_version_id)
    # No second completion, no resume for a finished upload.
    assert client.get(address).status_code == 409


def test_other_users_cannot_resume_complete_or_discard(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    other = make_user(workspace, "e3@example.com", "manager")
    client.force_login(other)
    args = ("version_id", info["version"])
    assert client.get(url("upload_resume", workspace, setup, args)).status_code == 404
    body = {"s3_key": info["s3_key"], "upload_id": info["upload_id"]}
    assert post_json(client, url("upload_complete", workspace, setup, args), body).status_code == 404
    assert post_json(client, url("upload_abort", workspace, setup, args), body).status_code == 404
    assert [c for c in ff.calls if c[0] == other.pk] == []


def test_discard_removes_the_unfinished_version(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    response = post_json(
        client,
        url("upload_abort", workspace, setup, ("version_id", info["version"])),
        {"s3_key": info["s3_key"], "upload_id": info["upload_id"]},
    )
    assert response.status_code == 200
    abort = [c for c in ff.calls if c[2] == "/upload/abort"][0][3]
    assert abort["discard"] is True
    assert not VideoVersion.objects.exists() and VideoAsset.objects.count() == 1
    assert start(client, workspace, setup).status_code == 200  # the channel is free again


def test_discard_keeps_and_syncs_version_when_freeframe_reports_it_completed(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    ff.abort_keeps_version = True
    response = post_json(
        client,
        url("upload_abort", workspace, setup, ("version_id", info["version"])),
        {"s3_key": info["s3_key"], "upload_id": info["upload_id"]},
    )
    assert response.status_code == 200, response.content
    assert response.json() == {"discarded": False, "status": "processing"}
    version = VideoVersion.objects.get()
    assert version.status == "processing" and str(version.ff_version_id) in [
        item["id"] for item in ff.versions[str(version.video.ff_asset_id)]
    ]


def test_discard_keeps_local_version_when_remote_state_cannot_be_confirmed(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    ff.fail[("GET", "/assets/")] = 503
    response = post_json(
        client,
        url("upload_abort", workspace, setup, ("version_id", info["version"])),
        {"s3_key": info["s3_key"], "upload_id": info["upload_id"]},
    )
    assert response.status_code == 503
    assert VideoVersion.objects.get().status == "uploading"


def test_discard_accepts_missing_remote_asset_as_confirmation(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    ff.fail[("GET", "/assets/")] = 404  # FreeFrame removed the empty asset on discard.
    response = post_json(
        client,
        url("upload_abort", workspace, setup, ("version_id", info["version"])),
        {"s3_key": info["s3_key"], "upload_id": info["upload_id"]},
    )
    assert response.status_code == 200 and response.json() == {"discarded": True}
    assert not VideoVersion.objects.exists() and not VideoAsset.objects.exists()


# --- status ------------------------------------------------------------------------


def test_status_follows_freeframe_to_ready_and_copies_media_info(client, ff, workspace, user, setup):
    client.force_login(user)
    info = started(client, workspace, setup)
    version = VideoVersion.objects.get()
    VideoVersion.objects.filter(pk=version.pk).update(status="processing")
    asset = str(version.video.ff_asset_id)
    ff.versions[asset][0].update(
        processing_status="ready",
        files=[{"width": 1920, "height": 1080, "duration_seconds": 60.0, "fps": 25.0}],
    )
    response = client.get(url("upload_status", workspace, setup))
    assert response.status_code == 200 and response.json()["fresh"] is True
    row = response.json()["channels"][0]["versions"][0]
    assert row["status"] == "ready" and row["status_label"] == "Ready"
    assert row["resolution"] == "1920×1080" and row["duration"] == "1:00"
    version.refresh_from_db()
    assert (version.width, version.height, version.fps) == (1920, 1080, 25.0)
    assert info["version"] == str(version.pk)


def test_status_keeps_stored_state_when_freeframe_is_down(client, ff, workspace, user, setup):
    client.force_login(user)
    started(client, workspace, setup)
    VideoVersion.objects.update(status="processing")
    ff.fail[("GET", "/assets/")] = 503
    response = client.get(url("upload_status", workspace, setup))
    assert response.status_code == 200 and response.json()["fresh"] is False
    assert response.json()["channels"][0]["versions"][0]["status"] == "processing"


def test_version_removed_in_freeframe_becomes_error(client, ff, workspace, user, setup):
    client.force_login(user)
    started(client, workspace, setup)
    asset = str(VideoAsset.objects.get().ff_asset_id)
    ff.versions[asset] = []
    response = client.get(url("upload_status", workspace, setup))
    assert response.json()["channels"][0]["versions"][0]["status"] == "failed"


def test_member_without_upload_right_sees_versions_but_no_button(client, ff, workspace, user, setup):
    client.force_login(user)
    started(client, workspace, setup)
    viewer = make_user(workspace, "v@example.com", "viewer")
    client.force_login(viewer)
    card = client.get(url("upload_status", workspace, setup)).json()["channels"][0]
    assert card["can_upload"] is False and len(card["versions"]) == 1
    page = client.get(
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": setup.id})
        + "?tab=video"
    )
    assert b"Upload version" not in page.content and b"reel.mp4" in page.content


def test_video_tab_has_the_button_for_editors_and_a_script_with_nonce(client, ff, workspace, user, setup):
    client.force_login(user)
    page = client.get(
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": setup.id})
        + "?tab=video"
    )
    html = page.content.decode()
    assert "Upload version" in html and 'accept=".m4v,.mkv,.mov,.mp4,.webm"' in html
    assert "jrc_video/upload.js" in html and "<script>" not in html.replace("<script >", "")
    assert "ff-access" not in html and "Bearer" not in html


def test_status_is_not_cached_and_never_carries_tokens(client, ff, workspace, user, setup):
    client.force_login(user)
    response = client.get(url("upload_status", workspace, setup))
    assert "no-store" in response["Cache-Control"] or "no-cache" in response["Cache-Control"]
    assert b"token" not in response.content.lower()
