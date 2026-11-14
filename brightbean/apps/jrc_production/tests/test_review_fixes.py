"""P2-B09 review fixes: lock order of copies, trash seen by maintenance, bounded search, error texts."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.db import connections, transaction
from django.urls import reverse
from django.utils import timezone

from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import search
from apps.jrc_production.services import create_content, pipeline_stage
from apps.jrc_production.tests.test_board_actions_trash_search import batch, content, role  # noqa: F401
from apps.jrc_production.tests.test_composer_captions import concurrent_database  # noqa: F401
from apps.jrc_texts.models import TextVersion
from apps.jrc_video.models import VideoAsset
from apps.social_accounts.models import SocialAccount
from apps.social_accounts.tests.test_youtube_compliance import channel, provider_for, video  # noqa: F401, F811
from apps.social_accounts.youtube_compliance import check_connection

pytestmark = pytest.mark.django_db


# R1: the default manager of copies must not join the content, or select_for_update() locks it too.
def test_copy_manager_has_no_join_so_row_locks_stay_on_the_copy():
    sql, _ = PlatformPost.objects.select_for_update().filter(pk=uuid4()).query.get_compiler("default").as_sql()
    assert "JOIN" not in sql.upper()
    assert "composer_post" in sql  # the trash filter is a subquery


@pytest.mark.django_db(transaction=True)
def test_locking_a_copy_does_not_lock_its_content(content, concurrent_database):  # noqa: F811
    copy = content.platform_posts.get()
    locked, release = Event(), Event()

    def hold_copy():
        try:
            with transaction.atomic():
                PlatformPost.objects.select_for_update().get(pk=copy.pk)
                locked.set()
                assert release.wait(8)
        finally:
            connections.close_all()

    def lock_content_nowait():
        try:
            with transaction.atomic():
                return list(Post.objects.select_for_update(nowait=True).filter(pk=content.pk).values_list("pk"))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_copy)
        try:
            assert locked.wait(8)
            # Would raise OperationalError (lock not available) if the copy lock also held the content row.
            assert pool.submit(lock_content_nowait).result(timeout=10) == [(content.pk,)]
        finally:
            release.set()
        holder.result(timeout=10)


# R2: maintenance sees copies of trashed content.
def test_youtube_cleanup_and_verification_see_trashed_copies(channel):  # noqa: F811
    old = video(channel)
    Post.objects.filter(pk=old.post_id).update(trashed_at=timezone.now())
    assert not PlatformPost.objects.filter(pk=old.pk).exists()
    check_connection(channel, provider=provider_for(channel))
    assert not PlatformPost.all_objects.filter(pk=old.pk).exists()
    assert Post.all_objects.filter(pk=old.post_id).exists()


def test_disconnecting_a_channel_removes_its_orphaned_trashed_content(client, user, workspace):
    account = SocialAccount.objects.create(
        workspace=workspace, platform="facebook", account_platform_id="r2", account_name="R2", oauth_access_token="t"
    )
    post = Post.objects.create(workspace=workspace, caption="trashed orphan")
    PlatformPost.objects.create(post=post, social_account=account)
    Post.objects.filter(pk=post.pk).update(trashed_at=timezone.now())
    client.force_login(user)
    with patch("providers.get_provider", return_value=MagicMock()):
        client.post(
            reverse("social_accounts:disconnect", kwargs={"workspace_id": workspace.id, "account_id": account.id})
        )
    assert not SocialAccount.objects.filter(pk=account.pk).exists()
    assert not Post.all_objects.filter(pk=post.pk).exists()


# R3: search stays bounded.
def test_search_skips_videos_of_already_matched_content_and_caps_requests(content, workspace, user, client):  # noqa: F811
    content.title = "Needle already matched"
    content.save()
    VideoAsset.objects.create(platform_post=content.platform_posts.get(), ff_asset_id=uuid4())
    for index in range(search.MAX_VIDEO_REQUESTS + 3):
        extra = create_manual_channel(workspace, "custom", f"Video channel {index}")
        other = create_content(workspace, f"Other {index}", user, social_account_ids=[extra.pk])
        VideoAsset.objects.create(platform_post=other.platform_posts.get(), ff_asset_id=uuid4())
    client.force_login(user)
    with patch("apps.jrc_production.search.FreeFrameUserClient.request", return_value=[]) as api:
        response = client.get(reverse("jrc_production:board"), {"q": "needle already"})
    assert api.call_count == search.MAX_VIDEO_REQUESTS
    assert response.context["total"] == 1
    assert "newest videos" in response.context["search_warning"]


def test_search_stops_asking_after_a_video_matched(content, workspace, user, client):  # noqa: F811
    VideoAsset.objects.create(platform_post=content.platform_posts.get(), ff_asset_id=uuid4())
    client.force_login(user)
    with patch(
        "apps.jrc_production.search.FreeFrameUserClient.request",
        return_value=[{"body": "no", "replies": [{"body": "ReplyNeedle"}]}, {"body": "ReplyNeedle too"}],
    ):
        response = client.get(reverse("jrc_production:board"), {"q": "replyneedle"})
    assert response.context["total"] == 1
    assert not response.context["search_warning"]


def test_text_versions_are_filtered_in_the_database(content, user, django_assert_num_queries):  # noqa: F811
    from apps.jrc_texts.services import create_initial_concept

    create_initial_concept(content, user, "Hidden SpecialNeedle here")
    create_initial_concept(create_content(content.workspace, "Other", user), user, "unrelated words")
    posts = list(Post.objects.filter(workspace=content.workspace))
    seen = []
    original = search.content_to_plain_text

    def spy(value):
        seen.append(value)
        return original(value)

    with (
        patch("apps.jrc_production.search.content_to_plain_text", spy),
        patch("apps.jrc_production.search.FreeFrameUserClient.request", return_value=[]),
    ):
        found, _ = search.search_content(posts, user, "specialneedle")
    assert [post.pk for post in found] == [content.pk]
    assert len(seen) <= 1  # the unrelated version never reaches Python
    assert TextVersion.objects.count() >= 2


# R4: error text and status codes; no-op drag.
def test_trash_error_is_text_not_a_list(client, user, workspace, content):  # noqa: F811
    client.force_login(user)
    copy = content.platform_posts.get()
    copy.status = "publishing"
    copy.save()
    response = client.post(
        reverse("composer:post_delete", kwargs={"workspace_id": workspace.id, "post_id": content.id})
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert not error.startswith("[")
    assert "publishing" in error


def test_trash_without_rights_is_403(client, user, workspace, content):  # noqa: F811
    role(workspace, user, "viewer")
    client.force_login(user)
    response = client.post(
        reverse("composer:post_delete", kwargs={"workspace_id": workspace.id, "post_id": content.id})
    )
    assert response.status_code == 403
    assert Post.objects.filter(pk=content.pk).exists()


def test_drag_to_approved_while_already_in_review_says_nothing_changed(content, workspace, user, client):  # noqa: F811
    role(workspace, user, "editor")
    first = batch(client, user, [content], "move", stage="approved").json()["results"][0]
    assert first["ok"] and "Approval requested" in first["message"]
    content.refresh_from_db()
    assert pipeline_stage(content) == "review"
    second = batch(client, user, [content], "move", stage="approved").json()["results"][0]
    assert second["ok"]
    assert "Nothing changed" in second["message"]
    assert "requested" not in second["message"].lower()
