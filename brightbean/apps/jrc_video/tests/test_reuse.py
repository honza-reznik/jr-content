# JR Content: přidáno 2026-11-14 (blok P2-B15), AGPL-3.0.
"""P2-B15: “Use for another output…” – one uploaded version as a new version of another output.

FreeFrame is replaced by a recording fake (its own side is covered by
``freeframe/apps/api/tests/test_jrc_duplicate_version.py``): these tests check what Brightbean asks
for, for whom, what it refuses before asking, what it records and how the Video tab, History,
notifications and the approval rules follow.
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
from apps.jrc_production.services import add_content_channel, create_content
from apps.jrc_video import player, reuse, uploads
from apps.jrc_video.client import FreeFrameError
from apps.jrc_video.models import VideoAccount, VideoAsset, VideoProject, VideoVersion
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


class FakeFreeFrame:
    """Answers ``POST /jrc/versions/{id}/duplicate`` like FreeFrame and records every call."""

    def __init__(self):
        self.calls = []
        self.fail = None
        self.numbers = {}

    def __call__(self, user):
        fake = self

        class Client:
            def request(self, method, path, payload=None):
                fake.calls.append((user.pk, method, path, payload))
                if fake.fail:
                    raise FreeFrameError("fail", status_code=fake.fail)
                asset = payload.get("asset_id") or str(uuid.uuid4())
                fake.numbers[asset] = fake.numbers.get(asset, 0) + 1
                return {
                    "asset_id": asset,
                    "version_id": str(uuid.uuid4()),
                    "version_number": fake.numbers[asset],
                    "comments_copied": 4 if payload["copy_comments"] else 0,
                }

        return Client()


@pytest.fixture
def ff(monkeypatch):
    fake = FakeFreeFrame()
    monkeypatch.setattr(reuse, "FreeFrameUserClient", fake)
    return fake


@pytest.fixture(autouse=True)
def _channels_on():
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False):
        yield


@pytest.fixture
def workspace(organization, user):
    workspace = Workspace.objects.create(organization=organization, name="Reuse test")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="manager")
    VideoAccount.objects.create(user=user, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    VideoProject.objects.create(workspace=workspace, ff_project_id=uuid.uuid4(), synced_at=timezone.now())
    return workspace


def make_user(workspace, email, role):
    person = User.objects.create_user(email=email, password="x", name=email, tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
    VideoAccount.objects.create(user=person, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    return person


@pytest.fixture
def world(workspace, user):
    """A content with three outputs: YouTube · Video (has v1 and v2), YouTube · Shorts, Instagram · Reel."""
    youtube = create_manual_channel(workspace, "youtube", "Ella YouTube")
    instagram = create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Garden tour", user, social_account_ids=[youtube.pk, instagram.pk])
    video_output = post.platform_posts.get(social_account=youtube)
    reel = post.platform_posts.get(social_account=instagram)
    shorts = add_content_channel(post, youtube.pk, user, "short_form")
    asset = VideoAsset.objects.create(platform_post=video_output, ff_asset_id=uuid.uuid4())
    v1 = VideoVersion.objects.create(
        video=asset,
        number=1,
        ff_version_id=uuid.uuid4(),
        uploaded_by=user,
        status="ready",
        original_filename="garden.mov",
        size_bytes=31_000_000,
        duration_s=62.0,
        fps=30.0,
        width=1080,
        height=1920,
    )
    v2 = VideoVersion.objects.create(
        video=asset,
        number=2,
        ff_version_id=uuid.uuid4(),
        uploaded_by=user,
        status="ready",
        original_filename="garden-v2.mov",
        size_bytes=32_000_000,
        duration_s=61.0,
        fps=30.0,
        width=1080,
        height=1920,
    )
    return dict(post=post, video_output=video_output, shorts=shorts, reel=reel, asset=asset, v1=v1, v2=v2)


def address(workspace, post, version):
    return reverse(
        "jrc_video:version_duplicate",
        kwargs={"workspace_id": workspace.id, "post_id": post.id, "version_id": version.pk},
    )


def use(client, workspace, world, version=None, target=None, keep=False):
    return client.post(
        address(workspace, world["post"], version or world["v2"]),
        json.dumps({"target": str((target or world["shorts"]).pk), "keep_comments": keep}),
        content_type="application/json",
    )


def test_output_without_video_gets_a_ready_version_sharing_the_file(client, ff, workspace, user, world):
    client.force_login(user)
    response = use(client, workspace, world)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["number"] == 1 and body["target"] == str(world["shorts"].pk)
    assert body["target_label"] == "YouTube · Shorts · Ella YouTube" and body["comments_copied"] is None
    # One call, as the person, naming the source version; a new FreeFrame asset for the output.
    assert len(ff.calls) == 1
    caller, method, path, payload = ff.calls[0]
    assert caller == user.pk and method == "POST"
    assert path == f"/jrc/versions/{world['v2'].ff_version_id}/duplicate"
    assert payload == {"copy_comments": False, "asset_name": "Garden tour · YouTube · Shorts · Ella YouTube"}
    copy = VideoVersion.objects.get(pk=body["version"])
    assert copy.video.platform_post == world["shorts"] and copy.status == "ready"  # no second conversion
    assert copy.copied_from == world["v2"] and copy.copied_from_label == "v2 · YouTube · Video · Ella YouTube"
    assert copy.uploaded_by == user and copy.comments_copied is None
    for field in ("original_filename", "size_bytes", "duration_s", "fps", "width", "height"):
        assert getattr(copy, field) == getattr(world["v2"], field), field
    assert copy.video.final_version is None  # Final is decided per output
    # The source is untouched.
    world["asset"].refresh_from_db()
    assert world["asset"].versions.count() == 2


def test_output_with_video_gets_the_next_version_of_its_own_asset(client, ff, workspace, user, world):
    client.force_login(user)
    first = use(client, workspace, world, version=world["v1"]).json()
    shorts_asset = VideoAsset.objects.get(platform_post=world["shorts"])
    second = use(client, workspace, world, version=world["v2"], keep=True)
    assert second.status_code == 200, second.content
    assert second.json()["number"] == 2 and first["number"] == 1
    assert ff.calls[-1][3] == {"copy_comments": True, "asset_id": str(shorts_asset.ff_asset_id)}
    assert VideoAsset.objects.filter(platform_post=world["shorts"]).count() == 1
    assert second.json()["comments_copied"] == 4
    assert VideoVersion.objects.get(pk=second.json()["version"]).comments_copied == 4


def test_branches_evolve_separately(client, ff, workspace, user, world):
    client.force_login(user)
    copy = VideoVersion.objects.get(pk=use(client, workspace, world).json()["version"])
    manager = WorkspaceMembership.objects.get(user=user, workspace=workspace)
    player.set_final(user, world["post"], manager, copy.pk)
    world["asset"].refresh_from_db()
    assert world["asset"].final_version is None  # marking the copy final leaves the source alone
    player.set_final(user, world["post"], manager, world["v1"].pk)
    copy.video.refresh_from_db()
    assert copy.video.final_version == copy
    # Deleting the source output locally keeps the copy; it only loses the link, not its name.
    world["video_output"].delete()
    copy.refresh_from_db()
    assert copy.copied_from is None and copy.copied_from_label == "v2 · YouTube · Video · Ella YouTube"


@pytest.mark.parametrize("keep", [None, "yes", 1])
def test_keep_comments_must_be_answered(client, ff, workspace, user, world, keep):
    client.force_login(user)
    response = client.post(
        address(workspace, world["post"], world["v2"]),
        json.dumps({"target": str(world["shorts"].pk), **({} if keep is None else {"keep_comments": keep})}),
        content_type="application/json",
    )
    assert response.status_code == 400 and response.json()["error"] == "Choose whether to keep the comments."
    assert ff.calls == []


def test_refusals_before_asking_freeframe(client, ff, workspace, user, world, organization):
    client.force_login(user)
    other = create_content(workspace, "Other", user, social_account_ids=[world["reel"].social_account_id])
    other_output = other.platform_posts.get()
    converting = VideoVersion.objects.create(
        video=world["asset"], number=3, ff_version_id=uuid.uuid4(), uploaded_by=user, status="processing"
    )
    cases = [
        (dict(target=world["video_output"]), 404),  # the source output itself
        (dict(target=other_output), 404),  # an output of another content
        (dict(version=converting), 409),  # not converted yet
    ]
    for kwargs, status in cases:
        response = use(client, workspace, world, **kwargs)
        assert response.status_code == status, (kwargs, response.content)
    missing = client.post(
        reverse(
            "jrc_video:version_duplicate",
            kwargs={"workspace_id": workspace.id, "post_id": world["post"].id, "version_id": uuid.uuid4()},
        ),
        json.dumps({"target": str(world["shorts"].pk), "keep_comments": False}),
        content_type="application/json",
    )
    assert missing.status_code == 404
    # A switched-off channel is not a target.
    channel = world["reel"].social_account.jrc_channel
    channel.is_enabled = False
    channel.save(update_fields=["is_enabled"])
    assert use(client, workspace, world, target=world["reel"]).status_code == 404
    assert ff.calls == []
    assert not VideoAsset.objects.filter(platform_post__in=[world["shorts"], world["reel"]]).exists()


@pytest.mark.parametrize("role", ["client", "viewer"])
def test_client_and_viewer_cannot_use_a_version_even_directly(client, ff, workspace, world, role):
    person = make_user(workspace, f"{role}@example.com", role)
    client.force_login(person)
    assert use(client, workspace, world).status_code == 403
    assert ff.calls == []


def test_contributor_only_on_own_content_like_uploading(client, ff, workspace, user, world):
    contributor = make_user(workspace, "contrib@example.com", "contributor")
    client.force_login(contributor)
    assert use(client, workspace, world).status_code == 403
    world["post"].author = contributor
    world["post"].save(update_fields=["author"])
    membership = WorkspaceMembership.objects.get(user=contributor)
    assert uploads.can_upload(world["post"], membership) == reuse.can_upload(world["post"], membership)
    if uploads.can_upload(world["post"], membership):
        assert use(client, workspace, world).status_code == 200


def test_archived_workspace_and_non_member(client, ff, workspace, user, world, organization):
    stranger = User.objects.create_user(email="s@example.com", password="x", name="S", tos_accepted_at=timezone.now())
    client.force_login(stranger)
    assert use(client, workspace, world).status_code == 403
    client.force_login(user)
    Workspace.objects.filter(pk=workspace.pk).update(is_archived=True)
    assert use(client, workspace, world).status_code == 403
    assert ff.calls == []


@pytest.mark.parametrize(
    ("status", "expected"), [(403, 403), (404, 404), (409, 409), (400, 400), (429, 429), (500, 503), (None, 503)]
)
def test_freeframe_refusals_become_plain_messages_and_leave_nothing(
    client, ff, workspace, user, world, status, expected
):
    client.force_login(user)
    ff.fail = status or 502
    response = use(client, workspace, world)
    assert response.status_code == (expected if status else 503)
    assert "error" in response.json() and "fail" not in response.json()["error"]
    assert not VideoAsset.objects.filter(platform_post=world["shorts"]).exists()


def test_unexpected_freeframe_answer_is_refused(client, ff, workspace, user, world, monkeypatch):
    client.force_login(user)

    class Odd:
        def __init__(self, _user):
            pass

        def request(self, method, path, payload=None):
            return {"asset_id": "nope", "version_id": str(uuid.uuid4()), "version_number": 1, "comments_copied": 0}

    monkeypatch.setattr(reuse, "FreeFrameUserClient", Odd)
    assert use(client, workspace, world).status_code == 502
    assert not VideoVersion.objects.filter(video__platform_post=world["shorts"]).exists()


def test_video_tab_feed_offers_the_action_and_names_the_source(client, ff, workspace, user, world):
    client.force_login(user)
    use(client, workspace, world, keep=True)
    feed = client.get(
        reverse("jrc_video:upload_status", kwargs={"workspace_id": workspace.id, "post_id": world["post"].id})
    ).json()["channels"]
    by_key = {card["key"]: card for card in feed}
    source_rows = by_key[str(world["video_output"].pk)]["versions"]
    assert all(row["can_reuse"] for row in source_rows)
    copy_row = by_key[str(world["shorts"].pk)]["versions"][0]
    assert copy_row["copied_from"] == "v2 · YouTube · Video · Ella YouTube" and copy_row["comments_copied"] == 4
    page = client.get(
        reverse("jrc_production:content_detail", args=[workspace.id, world["post"].pk]), {"tab": "video"}
    ).content.decode()
    # The version rows are drawn by video-tab.js from the feed above; the page carries the dialog.
    assert "data-video-reuse-dialog" in page and "Keep comments?" in page


@pytest.mark.parametrize("role", ["client", "viewer"])
def test_client_and_viewer_do_not_see_the_action(client, ff, workspace, user, world, role):
    client.force_login(user)
    use(client, workspace, world)
    person = make_user(workspace, f"{role}@example.com", role)
    client.force_login(person)
    feed = client.get(
        reverse("jrc_video:upload_status", kwargs={"workspace_id": workspace.id, "post_id": world["post"].id})
    ).json()["channels"]
    assert not any(row["can_reuse"] for card in feed for row in card["versions"])
    page = client.get(
        reverse("jrc_production:content_detail", args=[workspace.id, world["post"].pk]), {"tab": "video"}
    ).content.decode()
    assert "data-video-reuse-dialog" not in page


def test_single_output_has_no_action(client, ff, workspace, user):
    instagram = create_manual_channel(workspace, "instagram", "Solo")
    post = create_content(workspace, "Solo", user, social_account_ids=[instagram.pk])
    asset = VideoAsset.objects.create(platform_post=post.platform_posts.get(), ff_asset_id=uuid.uuid4())
    VideoVersion.objects.create(video=asset, number=1, ff_version_id=uuid.uuid4(), uploaded_by=user, status="ready")
    client.force_login(user)
    feed = client.get(
        reverse("jrc_video:upload_status", kwargs={"workspace_id": workspace.id, "post_id": post.id})
    ).json()["channels"]
    assert feed[0]["versions"][0]["can_reuse"] is False


def test_history_names_both_outputs(client, ff, workspace, user, world):
    client.force_login(user)
    use(client, workspace, world, keep=True)
    use(client, workspace, world, version=world["v1"], target=world["reel"])
    history = client.get(
        reverse("jrc_production:content_detail", args=[workspace.id, world["post"].pk]), {"tab": "history"}
    ).content.decode()
    assert (
        "Used video v2 · YouTube · Video · Ella YouTube for YouTube · Shorts · Ella YouTube as v1 · comments kept"
        in history
    )
    assert (
        "Used video v1 · YouTube · Video · Ella YouTube for Instagram · Reel · Ella Instagram as v1 · without comments"
        in history
    )
    assert "Uploaded video v2 · YouTube · Video" in history  # the upload itself stays listed


def test_notification_says_the_file_was_reused(client, ff, workspace, user, world, django_capture_on_commit_callbacks):
    from apps.notifications.models import Notification

    teammate = make_user(workspace, "editor@example.com", "editor")
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        use(client, workspace, world)
    note = Notification.objects.filter(user=teammate).latest("created_at")
    assert "used video v2 · YouTube · Video · Ella YouTube as version 1 for YouTube · Shorts" in note.body
    assert "uploaded" not in note.body


def test_approved_output_follows_the_edit_rules(client, ff, workspace, user, world):
    """Adding a version does not change what goes out (the final), like uploading one; marking it final does."""
    workspace.approval_workflow_mode = "required_internal"
    workspace.save(update_fields=["approval_workflow_mode"])
    PlatformPost.objects.filter(pk=world["shorts"].pk).update(status=PlatformPost.Status.APPROVED)
    client.force_login(user)
    response = use(client, workspace, world)
    assert response.status_code == 200 and "review_notice" not in response.json()
    world["shorts"].refresh_from_db()
    assert world["shorts"].status == PlatformPost.Status.APPROVED
    manager = WorkspaceMembership.objects.get(user=user, workspace=workspace)
    result = player.set_final(user, world["post"], manager, response.json()["version"])
    world["shorts"].refresh_from_db()
    assert world["shorts"].status == PlatformPost.Status.PENDING_REVIEW
    assert "back to review" in result["review_notice"]
