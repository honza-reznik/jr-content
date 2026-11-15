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
    response = client.get(
        reverse("jrc_production:board"), {"client": str(post.workspace_id), "channel": str(youtube.pk)}
    )
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


# ---------------------------------------------------------------------------
# Fixes after the review (R1–R4)
# ---------------------------------------------------------------------------


def _composer_save(client, workspace, post, account, action, **extra):
    when = timezone.localtime(timezone.now()) + timedelta(days=2)
    data = {
        "action": action,
        "title": post.title,
        "caption": post.caption,
        "selected_accounts": str(account.pk),
        "scheduled_date": when.strftime("%Y-%m-%d"),
        "scheduled_time": when.strftime("%H:%M"),
        **extra,
    }
    return client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/save/", data, HTTP_HX_REQUEST="true")


def _custom_with_two_outputs(workspace, user, first_status, second_status):
    from apps.composer.models import Post

    custom = create_manual_channel(workspace, "custom", "Forendors")
    post = Post.objects.create(workspace=workspace, author=user, title="Two outputs", caption="Same caption")
    first = PlatformPost.objects.create(
        post=post, social_account=custom, output_format="long_form", status=first_status
    )
    second = PlatformPost.objects.create(post=post, social_account=custom, output_format="post", status=second_status)
    return custom, post, first, second


@pytest.mark.parametrize("action", ["schedule", "publish_now"])
@pytest.mark.parametrize("cover", ["approved", "scheduled"])
@pytest.mark.parametrize("draft_first", [True, False])
def test_r1_composer_release_weighs_every_output_of_a_channel(client, workspace, user, action, cover, draft_first):
    """An approved or planned output must not carry its unapproved sibling on the same channel out."""
    workspace.approval_workflow_mode = "required_internal"
    workspace.save(update_fields=["approval_workflow_mode"])
    statuses = ("draft", cover) if draft_first else (cover, "draft")
    custom, post, first, second = _custom_with_two_outputs(workspace, user, *statuses)
    client.force_login(user)

    response = _composer_save(client, workspace, post, custom, action)

    assert response.status_code == 400
    assert "schedule" in response.json()["errors"]
    assert dict(post.platform_posts.values_list("output_format", "status")) == {
        "long_form": statuses[0],
        "post": statuses[1],
    }


def test_r1_composer_releases_two_approved_outputs_of_a_channel(client, workspace, user):
    workspace.approval_workflow_mode = "required_internal"
    workspace.save(update_fields=["approval_workflow_mode"])
    custom, post, first, second = _custom_with_two_outputs(workspace, user, "approved", "approved")
    client.force_login(user)

    response = _composer_save(client, workspace, post, custom, "schedule")

    assert response.status_code == 204, response.content
    assert set(post.platform_posts.values_list("status", flat=True)) == {"scheduled"}


# R2: going back with real migrations ----------------------------------------------------------


def _migrate_back_and_restore(target):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    try:
        MigrationExecutor(connection).migrate(target)
    finally:
        latest = MigrationExecutor(connection)
        latest.migrate(latest.loader.graph.leaf_nodes())


def _applied(app, name):
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder

    return (app, name) in MigrationRecorder(connection).applied_migrations()


def _snapshot():
    return (
        sorted(PlatformPost.objects.values_list("pk", "social_account_id", "output_format", "status")),
        sorted(ShotlistItem.objects.values_list("pk", "kind", "output_format", "text")),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", ["two outputs on a channel", "shotlist split", "format the old schema loses"])
def test_r2_going_back_is_refused_before_anything_is_dropped(workspace, user, youtube, instagram, case):
    from django.db import connection
    from django.db.migrations.exceptions import IrreversibleError
    from django.db.migrations.executor import MigrationExecutor

    if case == "two outputs on a channel":
        post = create_content(workspace, "Video and Shorts", user, social_account_ids=[youtube.pk])
        add_content_channel(post, youtube.pk, user, "short_form")
        shotlist_services.add_item(post, user, "Wide shot")
        shotlist_services.add_item(post, user, "Vertical hook", output_format="short_form")
        reason = "more than one output"
    elif case == "shotlist split":
        post = create_content(workspace, "Video and Reel", user, social_account_ids=[youtube.pk, instagram.pk])
        shotlist_services.add_item(post, user, "Wide shot")
        shotlist_services.add_item(post, user, "Vertical hook", output_format="short_form")
        reason = "shotlist item"
    else:
        post = create_content(workspace, "Story only", user)
        add_content_channel(post, instagram.pk, user, "story")
        reason = "format the old schema cannot keep"
    before = _snapshot()

    executor = MigrationExecutor(connection)
    try:
        with pytest.raises(IrreversibleError, match=reason):
            executor.migrate([("composer", "0024_content_trash")])
        # Refused at the first step back: nothing was unapplied, nothing dropped.
        assert _applied("jrc_production", "0009_shotlist_output_format")
        assert _applied("composer", "0025_platformpost_output_format")
        assert _snapshot() == before
    finally:
        latest = MigrationExecutor(connection)
        latest.migrate(latest.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_r2_going_back_and_forward_again_keeps_everything_when_nothing_would_be_lost(
    workspace, user, youtube, instagram
):
    post = create_content(workspace, "One output per channel", user, social_account_ids=[youtube.pk, instagram.pk])
    shotlist_services.add_item(post, user, "Wide shot")  # the first list (long form)
    before = _snapshot()

    _migrate_back_and_restore([("composer", "0024_content_trash")])

    assert _snapshot() == before


# R3: posting queues ---------------------------------------------------------------------------


@pytest.fixture
def queued_channel(workspace):
    from datetime import time

    from apps.calendar.models import PostingSlot, Queue

    custom = create_manual_channel(workspace, "custom", "Forendors")
    for day in range(7):
        PostingSlot.objects.create(social_account=custom, day_of_week=day, time=time(9, 0))
    return custom, Queue.objects.create(workspace=workspace, name="Forendors queue", social_account=custom)


def _two_outputs_on(workspace, user, account, status="draft"):
    from apps.composer.models import Post

    post = Post.objects.create(workspace=workspace, author=user, title="Two outputs", caption="Same caption")
    long_form = PlatformPost.objects.create(post=post, social_account=account, output_format="long_form", status=status)
    short = PlatformPost.objects.create(post=post, social_account=account, output_format="post", status=status)
    return post, long_form, short


@pytest.mark.parametrize("priority", [False, True])
def test_r3_queueing_content_with_two_outputs_on_the_channel_is_refused(workspace, user, queued_channel, priority):
    from apps.calendar.models import QueueEntry
    from apps.calendar.services import AmbiguousOutputError, add_to_queue

    custom, queue = queued_channel
    post, long_form, short = _two_outputs_on(workspace, user, custom)

    with pytest.raises(AmbiguousOutputError, match="more than one output"):
        add_to_queue(post, queue, priority=priority)

    assert not QueueEntry.objects.exists()
    assert set(post.platform_posts.values_list("scheduled_at", flat=True)) == {None}


@pytest.mark.parametrize("action", ["add_to_queue", "add_to_queue_priority"])
def test_r3_composer_refuses_to_queue_two_outputs_of_a_channel(client, workspace, user, queued_channel, action):
    from apps.calendar.models import QueueEntry

    custom, queue = queued_channel
    post, long_form, short = _two_outputs_on(workspace, user, custom)
    client.force_login(user)

    response = _composer_save(client, workspace, post, custom, action)

    assert response.status_code == 400
    assert "cannot tell them apart" in response.json()["errors"]["queue"]
    assert not QueueEntry.objects.exists()
    assert set(post.platform_posts.values_list("status", "scheduled_at")) == {("draft", None)}


def test_r3_a_queued_channel_does_not_take_a_second_output(workspace, user, queued_channel):
    from django.core.exceptions import ValidationError

    from apps.calendar.models import QueueEntry

    custom, queue = queued_channel
    post = create_content(workspace, "Queued", user, social_account_ids=[custom.pk])
    entry = QueueEntry.objects.create(queue=queue, post=post, position=0)

    with pytest.raises(ValidationError, match="posting queue"):
        add_content_channel(post, custom.pk, user, "story")

    assert post.platform_posts.count() == 1
    assert QueueEntry.objects.filter(pk=entry.pk).exists()


def _ambiguous_entry(workspace, user, queue):
    from apps.calendar.models import QueueEntry

    when = timezone.now() + timedelta(days=2)
    post, long_form, short = _two_outputs_on(workspace, user, queue.social_account, status="scheduled")
    PlatformPost.objects.filter(pk=long_form.pk).update(scheduled_at=when)
    PlatformPost.objects.filter(pk=short.pk).update(scheduled_at=when + timedelta(days=1))
    entry = QueueEntry.objects.create(queue=queue, post=post, position=0, assigned_slot_datetime=when)
    return post, entry


@pytest.mark.parametrize("endpoint", ["queue_entry_remove", "queue_entry_reslot", "queue_reorder"])
def test_r3_queue_endpoints_refuse_an_entry_that_cannot_name_its_output(
    client, workspace, user, queued_channel, endpoint
):
    from apps.calendar.models import QueueEntry

    custom, queue = queued_channel
    post, entry = _ambiguous_entry(workspace, user, queue)
    before = _snapshot()
    client.force_login(user)
    if endpoint == "queue_reorder":
        url = reverse("calendar:queue_reorder", args=[workspace.pk, queue.pk])
        data = {"entry_ids": str(entry.pk)}
    else:
        url = reverse(f"calendar:{endpoint}", args=[workspace.pk, queue.pk, entry.pk])
        data = {}

    response = client.post(url, data, HTTP_HX_REQUEST="true")

    assert response.status_code == 400
    assert "more than one output" in response.json()["error"]
    assert QueueEntry.objects.filter(pk=entry.pk).exists()
    assert _snapshot() == before


def test_r3_one_output_never_clears_the_queue_entry_its_sibling_may_hold(workspace, user, queued_channel):
    from apps.calendar.models import QueueEntry
    from apps.composer.services import transition_platform_post

    custom, queue = queued_channel
    post, entry = _ambiguous_entry(workspace, user, queue)
    long_form = post.platform_posts.get(output_format="long_form")
    short = post.platform_posts.get(output_format="post")

    PublishEngine._drop_queue_entry(short)  # published
    transition_platform_post(long_form, "draft")  # unscheduled

    assert QueueEntry.objects.filter(pk=entry.pk).exists()
    # A channel with one output of the content still frees its slot.
    PlatformPost.objects.filter(pk=long_form.pk).delete()
    short.refresh_from_db()
    PublishEngine._drop_queue_entry(short)
    assert not QueueEntry.objects.filter(pk=entry.pk).exists()


def test_r3_prioritizing_leaves_an_ambiguous_neighbour_where_it_is(workspace, user, queued_channel):
    from apps.calendar.services import prioritize

    custom, queue = queued_channel
    post, entry = _ambiguous_entry(workspace, user, queue)
    times = dict(post.platform_posts.values_list("pk", "scheduled_at"))
    other = create_content(workspace, "Single", user, social_account_ids=[custom.pk])

    prioritize(other, queue)

    assert dict(post.platform_posts.values_list("pk", "scheduled_at")) == times
    assert other.platform_posts.get().scheduled_at is not None


# R4: the format hint only where the provider publishes that type ------------------------------


@pytest.mark.parametrize(
    ("platform", "output_format", "media_type", "expected"),
    [
        ("facebook", "story", "image", PostType.IMAGE),
        ("facebook", "story", "video", PostType.VIDEO),
        ("facebook", "story", None, PostType.TEXT),
        ("facebook", "short_form", "video", PostType.REEL),
        ("instagram", "story", "image", PostType.STORY),
        ("youtube", "short_form", "video", PostType.SHORT),
    ],
)
def test_r4_format_hint_only_for_a_type_the_provider_publishes(platform, output_format, media_type, expected):
    from providers import PROVIDER_REGISTRY

    resolved = PublishEngine._resolve_post_type(
        platform=platform,
        platform_extra={},
        media_count=1 if media_type else 0,
        first_media_type=media_type,
        output_format=output_format,
        supported_post_types=PROVIDER_REGISTRY[platform]({}).supported_post_types,
    )
    assert resolved is expected


def test_r4_facebook_story_goes_out_with_its_image_not_as_text():
    from unittest.mock import patch

    from apps.publisher.test_media_handling import _attachment, _dispatch_mocks
    from providers.facebook import FacebookProvider

    engine, platform_post, provider = _dispatch_mocks(
        "facebook",
        needs_local_media=False,
        attachments=[_attachment("a1")],
        supported=FacebookProvider({}).supported_post_types,
    )
    platform_post.output_format = "story"
    with (
        patch("apps.publisher.engine.get_provider", return_value=provider),
        patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
    ):
        engine._dispatch_to_provider(platform_post)

    _token, content = provider.publish_post.call_args.args
    assert content.post_type is PostType.IMAGE
    assert content.media_urls == ["https://cdn.example/a.jpg"]
    # The real provider routes that to its photo endpoint, not to the text-or-link feed post.
    real = FacebookProvider({})
    content.extra["page_id"] = "page-1"
    with (
        patch.object(FacebookProvider, "_publish_photo", return_value="photo") as photo,
        patch.object(FacebookProvider, "_publish_text_or_link") as text,
    ):
        assert real.publish_post("token", content) == "photo"
    photo.assert_called_once()
    text.assert_not_called()
