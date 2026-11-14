"""P2-B14: outputs (channel + format) – several outputs of one content on one channel.

Covers the model (formats, defaults, uniqueness), the migration of existing copies without data loss,
the publisher's format hint, the detail tabs working per output, the board, the calendar, the shotlist
per format, the script sections, the API and the roles that may add outputs.
"""

import importlib
import uuid
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.composer import output_formats
from apps.composer.models import PlatformPost
from apps.composer.services import account_copy, clone_post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist as shotlist_services
from apps.jrc_production.models import ShotlistItem
from apps.jrc_production.services import add_content_channel, addable_outputs, create_content
from apps.jrc_texts import services as texts
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.members.models import WorkspaceMembership
from apps.publisher.engine import PublishEngine
from providers.types import PostType

pytestmark = pytest.mark.django_db


@pytest.fixture
def youtube(workspace):
    return create_manual_channel(workspace, "youtube", "Ella YT")


@pytest.fixture
def instagram(workspace):
    return create_manual_channel(workspace, "instagram", "Ella IG")


@pytest.fixture
def two_outputs(workspace, user, youtube):
    """A content with YouTube · Video and YouTube · Shorts on the same channel."""
    post = create_content(workspace, "Long and short", user, social_account_ids=[youtube.pk])
    shorts = add_content_channel(post, youtube.pk, user, "short_form")
    video = post.platform_posts.get(output_format="long_form")
    return post, video, shorts


def _member(workspace, django_user_model, role):
    member = django_user_model.objects.create_user(
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.test", password="x", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=member, workspace_role=role)
    return member


# ---------------------------------------------------------------------------
# Model and formats
# ---------------------------------------------------------------------------


def test_platform_formats_names_and_defaults():
    assert output_formats.default_format("youtube") == "long_form"
    assert output_formats.default_format("instagram") == "short_form"
    assert output_formats.default_format("custom") == "post"
    assert output_formats.format_name("youtube", "short_form") == "Shorts"
    assert output_formats.format_name("instagram", "short_form") == "Reel"
    assert output_formats.format_name("custom", "long_form") == "Long form"
    assert not output_formats.is_allowed("youtube", "story")
    assert output_formats.format_from_post_type("instagram", "story") == "story"
    assert output_formats.format_from_post_type("youtube", "story") == "long_form"  # not offered → default


def test_a_new_copy_without_format_gets_the_platform_default(workspace, user, youtube, instagram):
    post = create_content(workspace, "Defaults", user, social_account_ids=[youtube.pk, instagram.pk])
    formats = dict(post.platform_posts.values_list("social_account__platform", "output_format"))
    assert formats == {"youtube": "long_form", "instagram": "short_form"}
    # bulk_create (duplicate, recurrence, idea conversion) goes through the same default.
    PlatformPost.objects.filter(post=post).delete()
    PlatformPost.objects.bulk_create([PlatformPost(post=post, social_account=youtube)])
    assert post.platform_posts.get().output_format == "long_form"


def test_one_channel_carries_one_output_per_format(two_outputs, youtube, user):
    post, video, shorts = two_outputs
    assert {video.output_label, shorts.output_label} == {"YouTube · Video", "YouTube · Shorts"}
    assert shorts.output_title == "YouTube · Shorts · Ella YT"
    # The same output again is the existing one, not a duplicate.
    assert add_content_channel(post, youtube.pk, user, "short_form").pk == shorts.pk
    with pytest.raises(IntegrityError), transaction.atomic():
        PlatformPost.objects.create(post=post, social_account=youtube, output_format="short_form")
    assert post.platform_posts.count() == 2


def test_a_format_the_channel_does_not_offer_is_refused(workspace, user, youtube):
    from django.core.exceptions import ValidationError

    post = create_content(workspace, "No stories on YouTube", user, social_account_ids=[youtube.pk])
    with pytest.raises(ValidationError, match="format this channel offers"):
        add_content_channel(post, youtube.pk, user, "story")
    with pytest.raises(ValidationError):
        add_content_channel(post, youtube.pk, user, "nonsense")


def test_each_output_has_its_own_caption_video_and_date(two_outputs, user):
    post, video, shorts = two_outputs
    texts.save_caption(post.pk, video.pk, user, "Long caption")
    texts.save_caption(post.pk, shorts.pk, user, "Short caption #Shorts")
    asset_long = VideoAsset.objects.create(platform_post=video, ff_asset_id=uuid.uuid4())
    asset_short = VideoAsset.objects.create(platform_post=shorts, ff_asset_id=uuid.uuid4())
    assert asset_long.pk != asset_short.pk
    video.refresh_from_db()
    shorts.refresh_from_db()
    assert video.platform_specific_caption == "Long caption"
    assert shorts.platform_specific_caption == "Short caption #Shorts"


def test_duplicate_keeps_the_formats_and_old_paths_take_the_first_output(two_outputs, youtube, user):
    post, video, shorts = two_outputs
    copy = clone_post(post, author=user)
    assert sorted(copy.platform_posts.values_list("output_format", flat=True)) == ["long_form", "short_form"]
    # Upstream paths that know one copy per channel (old composer, CSV import) no longer fail.
    found, created = account_copy(post, youtube)
    assert (found.pk, created) == (video.pk, False)


def test_old_composer_delete_by_channel_removes_all_its_outputs(client, two_outputs, user):
    post, video, shorts = two_outputs
    client.force_login(user)
    response = client.post(
        reverse("composer:post_delete", args=[post.workspace_id, post.pk]) + f"?account={video.social_account_id}"
    )
    assert response.status_code in (200, 204, 302)
    assert not post.platform_posts.exists()


# ---------------------------------------------------------------------------
# Migration of existing copies (no data loss)
# ---------------------------------------------------------------------------


def test_migration_turns_every_copy_into_an_output_without_losing_work(workspace, user, youtube, instagram):
    migration = importlib.import_module("apps.composer.migrations.0025_platformpost_output_format")
    post = create_content(workspace, "Existing", user, social_account_ids=[youtube.pk, instagram.pk])
    other = create_content(workspace, "Existing story", user, social_account_ids=[instagram.pk])
    yt = post.platform_posts.get(social_account=youtube)
    ig = post.platform_posts.get(social_account=instagram)
    story = other.platform_posts.get()
    texts.save_caption(post.pk, ig.pk, user, "Keep me")
    asset = VideoAsset.objects.create(platform_post=yt, ff_asset_id=uuid.uuid4())
    VideoVersion.objects.create(video=asset, number=1, ff_version_id=uuid.uuid4(), uploaded_by=user)
    PlatformPost.objects.filter(pk=story.pk).update(platform_extra={"post_type": "story"})
    PlatformPost.objects.filter(pk=yt.pk).update(platform_extra={"post_type": "short"})
    # As before the migration: no format stored.
    PlatformPost.objects.update(output_format="")
    ids_before = set(PlatformPost.objects.values_list("pk", flat=True))

    migration.fill_formats(django_apps, None)

    assert set(PlatformPost.objects.values_list("pk", flat=True)) == ids_before
    assert dict(PlatformPost.objects.values_list("pk", "output_format")) == {
        yt.pk: "short_form",
        ig.pk: "short_form",
        story.pk: "story",
    }
    assert asset.versions.count() == 1 and VideoAsset.objects.get(pk=asset.pk).platform_post_id == yt.pk
    assert ig.jrc_texts.get().versions.filter(state="final").get().text == "Keep me"


def test_shotlist_migration_puts_the_existing_list_on_the_first_format(two_outputs, workspace, user):
    migration = importlib.import_module("apps.jrc_production.migrations.0009_shotlist_output_format")
    post, video, shorts = two_outputs
    bare = create_content(workspace, "No outputs", user)
    item = shotlist_services.add_item(post, user, "Wide shot")
    loose = shotlist_services.add_item(bare, user, "Idea shot")
    ShotlistItem.objects.update(output_format="")

    migration.assign_first_format(django_apps, None)

    item.refresh_from_db()
    loose.refresh_from_db()
    assert item.output_format == "long_form"
    assert loose.output_format == ""
    assert ShotlistItem.objects.get(post=post, kind="thumbnail").output_format == ""


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "output_format", "media_type", "expected"),
    [
        ("youtube", "short_form", "video", PostType.SHORT),
        ("youtube", "long_form", "video", PostType.VIDEO),
        ("instagram", "story", "image", PostType.STORY),
        ("instagram", "short_form", "video", PostType.REEL),
        ("instagram", "short_form", "image", PostType.IMAGE),  # a Reel needs one video: media decides
        ("facebook", "short_form", "video", PostType.REEL),
        ("custom", "post", "video", PostType.VIDEO),
    ],
)
def test_publisher_takes_the_post_type_from_the_output_format(platform, output_format, media_type, expected):
    resolved = PublishEngine._resolve_post_type(
        platform=platform,
        platform_extra={},
        media_count=1,
        first_media_type=media_type,
        output_format=output_format,
    )
    assert resolved is expected


def test_an_explicit_upstream_hint_still_wins_over_the_format():
    resolved = PublishEngine._resolve_post_type(
        platform="youtube",
        platform_extra={"post_type": "video"},
        media_count=1,
        first_media_type="video",
        output_format="short_form",
    )
    assert resolved is PostType.VIDEO


# ---------------------------------------------------------------------------
# Detail tabs per output
# ---------------------------------------------------------------------------


def _tab(client, post, tab, **params):
    return client.get(
        reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk]), {"tab": tab, **params}
    )


@pytest.mark.parametrize("tab", ["caption", "publish", "approval", "video"])
def test_tabs_list_each_output_by_its_name(client, two_outputs, user, tab):
    post, video, shorts = two_outputs
    client.force_login(user)
    response = _tab(client, post, tab)
    assert response.status_code == 200
    html = response.content.decode()
    assert "YouTube · Video · Ella YT" in html
    assert "YouTube · Shorts · Ella YT" in html


def test_video_tab_has_one_card_per_output(client, two_outputs, user):
    post, video, shorts = two_outputs
    client.force_login(user)
    response = _tab(client, post, "video")
    keys = [card["key"] for card in response.context["video_cards"]]
    assert keys == [str(video.pk), str(shorts.pk)]


def test_caption_autosave_writes_only_its_output(client, two_outputs, user):
    post, video, shorts = two_outputs
    client.force_login(user)
    url = reverse("jrc_production:caption_autosave", args=[post.workspace_id, post.pk, shorts.pk])
    response = client.post(
        url,
        {"text": "Only the Shorts", "device": "device-p2b14-0001"},
        content_type="application/json",
    )
    assert response.status_code in (200, 201)
    video.refresh_from_db()
    shorts.refresh_from_db()
    assert shorts.platform_specific_caption == "Only the Shorts"
    assert video.platform_specific_caption in (None, "")


def test_output_tags_offer_missing_formats_and_add_one(client, workspace, user, youtube, instagram):
    post = create_content(workspace, "Tags", user, social_account_ids=[youtube.pk])
    values = [option["value"] for option in addable_outputs(post)]
    assert f"{youtube.pk}:short_form" in values
    assert f"{youtube.pk}:long_form" not in values
    assert f"{instagram.pk}:story" in values
    client.force_login(user)
    response = client.post(
        reverse("jrc_production:channels", args=[workspace.pk, post.pk]),
        {"action": "add_channel", "channel": f"{instagram.pk}:story"},
    )
    assert response.status_code == 200
    assert "Story" in response.json()["html"]
    assert post.platform_posts.filter(social_account=instagram, output_format="story").exists()
    # A bare channel id (old form) adds the platform default.
    response = client.post(
        reverse("jrc_production:channels", args=[workspace.pk, post.pk]),
        {"action": "add_channel", "channel": str(instagram.pk)},
    )
    assert post.platform_posts.filter(social_account=instagram, output_format="short_form").exists()
    response = client.post(
        reverse("jrc_production:channels", args=[workspace.pk, post.pk]),
        {"action": "add_channel", "channel": f"{youtube.pk}:story"},
    )
    assert response.status_code == 400
    assert "format this channel offers" in response.json()["error"]


@pytest.mark.parametrize("role", ["client", "viewer"])
def test_client_and_viewer_cannot_add_outputs(client, workspace, user, youtube, django_user_model, role):
    post = create_content(workspace, "Not theirs", user, social_account_ids=[youtube.pk])
    member = _member(workspace, django_user_model, role)
    client.force_login(member)
    response = client.post(
        reverse("jrc_production:channels", args=[workspace.pk, post.pk]),
        {"action": "add_channel", "channel": f"{youtube.pk}:short_form"},
    )
    assert response.status_code == 403
    assert post.platform_posts.count() == 1


def test_removing_one_output_keeps_the_other(client, two_outputs, user):
    post, video, shorts = two_outputs
    client.force_login(user)
    response = client.post(
        reverse("jrc_production:channels", args=[post.workspace_id, post.pk]),
        {"action": "remove_channel", "platform_post": shorts.pk},
    )
    assert response.status_code == 200
    assert list(post.platform_posts.values_list("pk", flat=True)) == [video.pk]


def test_publication_dates_are_set_per_output(client, two_outputs, user, workspace):
    from apps.jrc_production.services import set_publication_date

    post, video, shorts = two_outputs
    workspace.approval_workflow_mode = "none"
    workspace.save(update_fields=["approval_workflow_mode"])
    for variant in (video, shorts):
        texts.save_caption(post.pk, variant.pk, user, "Caption")
    when_long = timezone.now() + timedelta(days=3)
    when_short = timezone.now() + timedelta(days=4)
    set_publication_date(post, video.pk, user, when_long)
    set_publication_date(post, shorts.pk, user, when_short)
    video.refresh_from_db()
    shorts.refresh_from_db()
    assert video.scheduled_at == when_long and shorts.scheduled_at == when_short


# ---------------------------------------------------------------------------
# Board and calendar
# ---------------------------------------------------------------------------


def test_board_shows_the_content_once_with_its_formats(client, two_outputs, user, youtube):
    post, video, shorts = two_outputs
    client.force_login(user)
    response = client.get(reverse("jrc_production:board"), {"client": str(post.workspace_id), "channel": str(youtube.pk)})
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count(f'data-post="{post.pk}"') == 1
    assert "Video, Shorts" in html


def test_calendar_shows_each_output_with_its_format(client, two_outputs, user, workspace):
    post, video, shorts = two_outputs
    when = timezone.now() + timedelta(days=2)
    PlatformPost.objects.filter(pk__in=[video.pk, shorts.pk]).update(status="scheduled", scheduled_at=when)
    client.force_login(user)
    url = reverse("calendar:calendar", args=[workspace.pk])
    for view in ("month", "week", "day"):
        response = client.get(url, {"mode": "calendar", "view": view, "date": when.date().isoformat()})
        assert response.status_code == 200
        html = response.content.decode()
        assert '<span class="calendar-format-label">Video</span>' in html, view
        assert '<span class="calendar-format-label">Shorts</span>' in html, view


def test_publish_queue_names_each_output(client, two_outputs, user, workspace):
    post, video, shorts = two_outputs
    when = timezone.now() + timedelta(days=2)
    PlatformPost.objects.filter(pk__in=[video.pk, shorts.pk]).update(status="scheduled", scheduled_at=when)
    client.force_login(user)
    response = client.get(reverse("calendar:publish_tab_queue", args=[workspace.pk]), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    html = response.content.decode()
    assert "YouTube · Video · Ella YT" in html
    assert "YouTube · Shorts · Ella YT" in html


# ---------------------------------------------------------------------------
# Shotlist per format and script sections
# ---------------------------------------------------------------------------


def test_shotlist_has_one_list_per_format(client, two_outputs, user):
    post, video, shorts = two_outputs
    long_item = shotlist_services.add_item(post, user, "Wide establishing shot")
    short_item = shotlist_services.add_item(post, user, "Vertical hook", output_format="short_form")
    assert long_item.output_format == "long_form"  # empty = the first list
    assert shotlist_services.shotlist_formats(post) == ["long_form", "short_form"]
    client.force_login(user)
    response = _tab(client, post, "shotlist")
    html = response.content.decode()
    assert 'data-shotlist-format="short_form"' in html
    assert "Wide establishing shot" in html and "Vertical hook" not in html
    response = _tab(client, post, "shotlist", format="short_form")
    html = response.content.decode()
    assert "Vertical hook" in html and "Wide establishing shot" not in html
    assert "Shotlist · Short form" in html
    # The thumbnail item (no format) shows in the first list only.
    assert "Thumbnail photo" not in html
    assert short_item.output_format == "short_form"


def test_shotlist_add_and_move_stay_in_their_format(client, two_outputs, user):
    post, video, shorts = two_outputs
    client.force_login(user)
    url = reverse("jrc_production:shotlist_action", args=[post.workspace_id, post.pk])
    for text in ("Hook", "Payoff"):
        response = client.post(
            url, {"action": "add", "text": text, "output_format": "short_form"}, HTTP_X_JRC_SHOTLIST="1"
        )
        assert response.status_code == 200
    payoff = ShotlistItem.objects.get(post=post, text="Payoff")
    response = client.post(
        url, {"action": "up", "item": payoff.pk, "output_format": "short_form"}, HTTP_X_JRC_SHOTLIST="1"
    )
    html = response.content.decode()
    assert html.index("Payoff") < html.index("Hook")
    assert "Thumbnail photo" not in html
    assert list(shotlist_services.items_for(post, "short_form")) == list(
        ShotlistItem.objects.filter(post=post, output_format="short_form").order_by("position")
    )
    response = client.post(url, {"action": "add", "text": "X", "output_format": "story"}, HTTP_X_JRC_SHOTLIST="1")
    assert response.status_code == 400


def test_first_output_takes_the_shotlist_written_before_it(workspace, user, instagram, youtube):
    post = create_content(workspace, "Idea first", user)
    item = shotlist_services.add_item(post, user, "Early idea shot")
    assert item.output_format == ""
    add_content_channel(post, instagram.pk, user, "short_form")
    item.refresh_from_db()
    assert item.output_format == "short_form"
    add_content_channel(post, youtube.pk, user, "long_form")
    item.refresh_from_db()
    assert item.output_format == "short_form"  # stays with its format when another one comes first


def test_script_offers_a_section_per_format(client, two_outputs, user, workspace, youtube):
    post, video, shorts = two_outputs
    client.force_login(user)
    html = _tab(client, post, "script").content.decode()
    assert 'data-script-section="Long form"' in html
    assert 'data-script-section="Short form"' in html
    single = create_content(workspace, "One format", user, social_account_ids=[youtube.pk])
    assert "data-script-section" not in _tab(client, single, "script").content.decode()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_api_schema_carries_the_output_format(two_outputs):
    from apps.api.schemas import PlatformPostSummary

    post, video, shorts = two_outputs
    summary = PlatformPostSummary.from_platform_post(shorts)
    assert summary.output_format == "short_form"
    assert summary.output_label == "YouTube · Shorts"
