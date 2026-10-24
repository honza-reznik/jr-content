"""F6-B02: composer and previews show the latest saved caption read-only; more channels on the Caption card."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from socket import getaddrinfo as resolve_database
from threading import Event
from time import monotonic

import pytest
from django.db import connection, connections, transaction
from django.db.models.deletion import RestrictedError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer import views as composer_views
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.jrc_production import views as production_views
from apps.jrc_production.services import create_content
from apps.jrc_texts.models import TextDocument
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db

# Emoji, line breaks, a blank line and more than 150 characters: nothing may be cut or reflowed.
LONG_CAPTION = 'Hi \U0001f331 garden!\n\nSecond line with a dash – and quotes "<b>" & more.\n' + "word " * 60 + "END"


@pytest.fixture
def content(workspace, user):
    instagram = create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Garden reel", user)
    variant = PlatformPost.objects.get(post=post, social_account=instagram)
    return post, variant, instagram


def _detail(post, tab="caption"):
    return (
        reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.id})
        + f"?tab={tab}"
    )


def _save_caption(client, variant, caption, *, base_version=""):
    return client.post(
        _detail(variant.post),
        {
            "action": "save_caption",
            "platform_post": str(variant.pk),
            "base_version": str(base_version),
            "caption": caption,
        },
    )


def _latest(variant):
    document = TextDocument.objects.get(platform_post=variant, kind=TextDocument.Kind.CAPTION)
    return document.versions.filter(state="final").order_by("-number").first()


def _composer_save(client, post, accounts, **extra):
    return client.post(
        reverse("composer:save_post_edit", kwargs={"workspace_id": post.workspace_id, "post_id": post.id}),
        {
            "action": "save_draft",
            "title": post.title,
            "caption": "Shared caption typed in the composer",
            "selected_accounts": ",".join(str(account.pk) for account in accounts),
            **extra,
        },
    )


def _composer_autosave(client, post, accounts, *, fallback=False, **extra):
    route = "composer:autosave" if fallback else "composer:autosave_edit"
    kwargs = {"workspace_id": post.workspace_id}
    if not fallback:
        kwargs["post_id"] = post.pk
    return client.post(
        reverse(route, kwargs=kwargs),
        {
            "_autosave_post_id": str(post.pk),
            "title": "Stale composer title",
            "caption": "Stale composer caption",
            "selected_accounts": ",".join(str(account.pk) for account in accounts),
            **extra,
        },
    )


def _assert_selection_rejected(response, endpoint):
    if endpoint == "save":
        assert response.status_code == 409
        message = response.json()["errors"]["selected_accounts"]
    else:
        assert response.status_code == 200
        assert b"Not saved" in response.content
        events = json.loads(response.headers["HX-Trigger"])
        assert "autosaved" not in events
        message = events["captionSelectionConflict"]["message"]
    assert "caption history cannot be removed" in message
    assert "Reload the composer" in message


@pytest.mark.parametrize("endpoint", ["save", "autosave", "autosave_fallback"])
@pytest.mark.parametrize("stale_list", [False, True], ids=["deselected", "stale-list"])
def test_composer_cannot_delete_caption_history(client, workspace, user, content, endpoint, stale_list):
    post, variant, instagram = content
    client.force_login(user)
    selected = []
    if stale_list:
        # A composer opened before this new channel was added still selects only Instagram.
        selected = [instagram]
        channel = create_manual_channel(workspace, "linkedin_personal", "Later LinkedIn")
        response = client.post(
            _detail(post), {"action": "save_caption", "platform_post": f"new:{channel.pk}", "caption": "New channel"}
        )
        assert response.status_code == 302
        variant = PlatformPost.objects.get(post=post, social_account=channel)
    else:
        assert _save_caption(client, variant, "First saved caption").status_code == 302
    assert _save_caption(client, variant, LONG_CAPTION, base_version=_latest(variant).pk).status_code == 302
    document = TextDocument.objects.get(platform_post=variant)
    before_versions = list(document.versions.values())
    before_post = (post.title, post.caption)

    if endpoint == "save":
        response = _composer_save(client, post, selected, title="Stale title")
    else:
        response = _composer_autosave(client, post, selected, fallback=endpoint == "autosave_fallback")

    _assert_selection_rejected(response, endpoint)
    post.refresh_from_db()
    variant.refresh_from_db()
    assert (post.title, post.caption) == before_post  # reject the entire save, not just its deletions
    assert variant.platform_specific_caption == LONG_CAPTION
    assert list(document.versions.values()) == before_versions
    assert TextDocument.objects.get(pk=document.pk).platform_post_id == variant.pk


@pytest.mark.parametrize("bulk", [False, True])
def test_caption_document_restricts_direct_copy_deletion(client, user, content, bulk):
    post, variant, instagram = content
    client.force_login(user)
    _save_caption(client, variant, LONG_CAPTION)
    document = TextDocument.objects.get(platform_post=variant)
    before = list(document.versions.values())

    with pytest.raises(RestrictedError), transaction.atomic():
        if bulk:
            PlatformPost.objects.filter(pk=variant.pk).delete()
        else:
            variant.delete()

    assert PlatformPost.objects.filter(pk=variant.pk).exists()
    assert list(document.versions.values()) == before


@pytest.mark.django_db(transaction=True)
def test_explicit_whole_content_deletion_still_removes_caption_history(client, user, content):
    post, variant, instagram = content
    client.force_login(user)
    _save_caption(client, variant, LONG_CAPTION)
    document = TextDocument.objects.get(platform_post=variant)
    with transaction.atomic():
        post.delete()
    assert not TextDocument.objects.filter(pk=document.pk).exists()
    assert not document.versions.exists()


def test_caption_guard_keeps_unversioned_deselection_working(client, user, content):
    post, variant, instagram = content
    client.force_login(user)
    assert _composer_save(client, post, []).status_code == 302
    assert not PlatformPost.objects.filter(pk=variant.pk).exists()


def test_scoped_composer_does_not_require_a_sibling_with_caption_history(client, workspace, user, content):
    post, variant, instagram = content
    client.force_login(user)
    _save_caption(client, variant, LONG_CAPTION)
    channel = create_manual_channel(workspace, "linkedin_personal", "Other channel")
    PlatformPost.objects.create(post=post, social_account=channel)

    response = _composer_save(client, post, [channel], account_scope=str(channel.pk))

    assert response.status_code == 302
    assert _latest(variant).text == LONG_CAPTION


@pytest.mark.parametrize("submitted", [None, "New video title", ""])
def test_versioned_caption_keeps_title_editable_and_preserves_an_absent_title(client, workspace, user, submitted):
    channel = create_manual_channel(workspace, "youtube", "Video channel")
    post = create_content(workspace, "Shared title", user)
    variant = PlatformPost.objects.get(post=post, social_account=channel)
    variant.platform_specific_title = 'Original title <b> & "quoted"'
    variant.save(update_fields=["platform_specific_title"])
    original = variant.platform_specific_title
    client.force_login(user)
    _save_caption(client, variant, LONG_CAPTION)
    page = _compose_page(client, post)
    assert page.context["jrc_captions"][str(channel.pk)]["title"] == original
    locked_panel = (
        page.content.decode().split("data-jrc-caption", 1)[1].split("<!-- Platform-specific overrides -->")[0]
    )
    assert ":name=\"'override_title_' + accId\"" in locked_panel

    extra = {} if submitted is None else {f"override_title_{channel.pk}": submitted}
    response = _composer_save(client, post, [channel], **extra)

    assert response.status_code == 302
    variant.refresh_from_db()
    assert variant.platform_specific_title == (original if submitted is None else submitted or None)
    assert variant.platform_specific_caption == LONG_CAPTION


def _thread_request(user, operation, pid_queue=None):
    """Each concurrent HTTP request uses and closes its own PostgreSQL connection."""
    try:
        http = Client()
        http.force_login(user)
        reported = False

        def report_connection(execute, sql, params, many, context):
            nonlocal reported
            if not reported:
                reported = True
                execute("SET lock_timeout = '8s'", None, False, context)
                if pid_queue is not None:
                    pid_queue.put(connection.connection.info.backend_pid)
            return execute(sql, params, many, context)

        # Client may reconnect at request start: report the request's connection,
        # rather than the earlier connection used by force_login.
        with connection.execute_wrapper(report_connection):
            return operation(http)
    finally:
        connections.close_all()


@pytest.fixture
def concurrent_database(_no_network, monkeypatch, settings):
    """Allow only PostgreSQL DNS for the workers' new connections; keep HTTP blocked."""
    import socket

    def database_only(host, port, *args, **kwargs):
        database = settings.DATABASES["default"]
        if host == database["HOST"] and str(port) == str(database["PORT"]):
            return resolve_database(host, port, *args, **kwargs)
        raise AssertionError("Production services must not access the network")

    monkeypatch.setattr(socket, "getaddrinfo", database_only)


def _wait_for_post_lock(pid, future):
    deadline = monotonic() + 5
    while monotonic() < deadline and not future.done():
        with connection.cursor() as cursor:
            cursor.execute("SELECT cardinality(pg_blocking_pids(%s)) > 0", [pid])
            if cursor.fetchone()[0]:
                return True
        Event().wait(0.01)
    return False


@pytest.mark.django_db(transaction=True)
def test_first_caption_save_waits_for_composer_and_cannot_be_overwritten(
    user, content, monkeypatch, concurrent_database
):
    post, variant, instagram = content
    snapshot_ready, continue_composer = Event(), Event()
    original = composer_views._versioned_caption_by_platform_post

    def pause_after_snapshot(ids):
        result = original(ids)
        snapshot_ready.set()
        assert continue_composer.wait(8)
        return result

    monkeypatch.setattr(composer_views, "_versioned_caption_by_platform_post", pause_after_snapshot)
    pid_queue = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        composer = pool.submit(
            _thread_request,
            user,
            lambda http: _composer_save(
                http, post, [instagram], **{f"override_caption_{instagram.pk}": "Old override"}
            ),
        )
        try:
            if not snapshot_ready.wait(8):
                composer.result(timeout=1)
                pytest.fail("Composer did not reach the caption snapshot")
            caption = pool.submit(
                _thread_request, user, lambda http: _save_caption(http, variant, LONG_CAPTION), pid_queue
            )
            blocked = _wait_for_post_lock(pid_queue.get(timeout=8), caption)
        finally:
            continue_composer.set()
        assert composer.result(timeout=10).status_code == 302
        assert caption.result(timeout=10).status_code == 302
    assert blocked, "The first Caption save must wait until the composer's stale snapshot is finished"
    variant.refresh_from_db()
    assert variant.platform_specific_caption == _latest(variant).text == LONG_CAPTION


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("endpoint", ["save", "autosave", "autosave_fallback"])
def test_stale_composer_waits_for_new_caption_channel_then_rejects_it(
    user, workspace, content, monkeypatch, endpoint, concurrent_database
):
    post, variant, instagram = content
    channel = create_manual_channel(workspace, "linkedin_personal", "Concurrent new channel")
    copy_ready, continue_caption = Event(), Event()
    original = production_views._create_missing_copy

    def pause_after_copy(*args, **kwargs):
        copy = original(*args, **kwargs)
        copy_ready.set()
        assert continue_caption.wait(8)
        return copy

    monkeypatch.setattr(production_views, "_create_missing_copy", pause_after_copy)
    pid_queue = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        caption = pool.submit(
            _thread_request,
            user,
            lambda http: http.post(
                _detail(post),
                {
                    "action": "save_caption",
                    "platform_post": f"new:{channel.pk}",
                    "caption": LONG_CAPTION,
                },
            ),
        )
        try:
            if not copy_ready.wait(8):
                caption.result(timeout=1)
                pytest.fail("Caption did not reach channel creation")

            def operation(http):
                if endpoint == "save":
                    return _composer_save(http, post, [instagram])
                return _composer_autosave(http, post, [instagram], fallback=endpoint == "autosave_fallback")

            composer = pool.submit(_thread_request, user, operation, pid_queue)
            blocked = _wait_for_post_lock(pid_queue.get(timeout=8), composer)
        finally:
            continue_caption.set()
        assert caption.result(timeout=10).status_code == 302
        response = composer.result(timeout=10)
    assert blocked, "Composer must wait for the Caption transaction before checking the selected channels"
    _assert_selection_rejected(response, endpoint)
    copy = PlatformPost.objects.get(post=post, social_account=channel)
    assert copy.platform_specific_caption == _latest(copy).text == LONG_CAPTION
    assert TextDocument.objects.get(platform_post=copy).versions.count() == 2


def _compose_page(client, post):
    return client.get(reverse("composer:compose_edit", kwargs={"workspace_id": post.workspace_id, "post_id": post.id}))


def _preview(client, post, accounts, **extra):
    return client.post(
        reverse("composer:preview", kwargs={"workspace_id": post.workspace_id}),
        {
            "caption": "Shared caption typed in the composer",
            "selected_accounts": ",".join(str(account.pk) for account in accounts),
            "_autosave_post_id": str(post.pk),
            **extra,
        },
    )


def test_composer_save_never_overwrites_a_saved_caption(client, user, content):
    post, variant, instagram = content
    client.force_login(user)
    assert _save_caption(client, variant, LONG_CAPTION).status_code == 302
    version = _latest(variant)

    # A crafted or stale form posts an override, an empty override, then none at all.
    for extra in (
        {f"override_caption_{instagram.pk}": "Overwrite from composer"},
        {f"override_caption_{instagram.pk}": ""},
        {},
    ):
        response = _composer_save(client, post, [instagram], **extra)
        assert response.status_code in (200, 302), response.content[:300]
        variant.refresh_from_db()
        assert variant.platform_specific_caption == LONG_CAPTION
        assert variant.effective_caption == LONG_CAPTION

    document = TextDocument.objects.get(platform_post=variant, kind=TextDocument.Kind.CAPTION)
    # Version 1 is the starting text of the document, version 2 the caption saved above.
    assert document.versions.filter(state="final").count() == 2
    assert _latest(variant).pk == version.pk
    assert _latest(variant).text == LONG_CAPTION


def test_composer_still_saves_an_override_for_a_channel_without_a_saved_version(client, user, content):
    post, variant, instagram = content
    client.force_login(user)

    response = _composer_save(client, post, [instagram], **{f"override_caption_{instagram.pk}": "Plain override"})

    assert response.status_code in (200, 302)
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Plain override"
    assert not TextDocument.objects.filter(platform_post=variant).exists()


def test_composer_shows_saved_caption_read_only_with_link_to_caption_tab(client, workspace, user, content):
    post, variant, instagram = content
    client.force_login(user)
    _save_caption(client, variant, LONG_CAPTION)
    other = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
    PlatformPost.objects.create(post=post, social_account=other)

    response = _compose_page(client, post)

    assert response.status_code == 200
    captions = response.context["jrc_captions"]
    assert set(captions) == {str(instagram.pk)}  # LinkedIn has no saved version: unchanged behavior
    assert captions[str(instagram.pk)]["text"] == LONG_CAPTION
    assert captions[str(instagram.pk)]["version"] == 2
    assert captions[str(instagram.pk)]["edit_url"] == _detail(post)
    html = response.content.decode()
    payload = re.search(r'<script id="composer-jrc-captions" type="application/json">(.*?)</script>', html, re.S)
    assert json.loads(payload.group(1))[str(instagram.pk)]["text"] == LONG_CAPTION
    assert "Edit in Caption tab" in html
    assert re.search(r"<textarea\s+readonly", html)
    # No editable override field is bound to a channel with a saved caption.
    assert "jrcCaptions[id]" in html


def test_composer_has_no_read_only_caption_without_a_saved_version(client, user, content):
    post, variant, instagram = content
    client.force_login(user)

    response = _compose_page(client, post)

    assert response.status_code == 200
    assert response.context["jrc_captions"] == {}


def test_composer_preview_uses_latest_saved_version_not_posted_text(client, user, content):
    post, variant, instagram = content
    client.force_login(user)
    _save_caption(client, variant, "First version")
    _save_caption(client, variant, LONG_CAPTION, base_version=_latest(variant).pk)

    response = _preview(client, post, [instagram], **{f"override_caption_{instagram.pk}": "Typed override"})

    assert response.status_code == 200
    html = response.content.decode()
    assert "Typed override" not in html
    assert "Shared caption typed in the composer" not in html
    # The Instagram preview carries the whole text (no 150 character cut), line breaks included.
    assert "Hi \U0001f331 garden!\n\nSecond line with a dash – and quotes &quot;&lt;b&gt;&quot; &amp; more.\n" in html
    assert "word word word" in html
    assert "END" in html
    assert "…" not in html.split("whitespace-pre-wrap break-words")[1].split("</span>")[0]
    assert f"{instagram.caption_wire_length(LONG_CAPTION)}/2200" in html


def test_composer_preview_without_saved_version_keeps_posted_text(client, user, content):
    post, variant, instagram = content
    client.force_login(user)

    response = _preview(client, post, [instagram])

    assert "Shared caption typed in the composer" in response.content.decode()


def test_detail_instagram_preview_matches_latest_version(client, user, content):
    post, variant, instagram = content
    client.force_login(user)
    _save_caption(client, variant, "Old text")
    _save_caption(client, variant, LONG_CAPTION, base_version=_latest(variant).pk)

    response = client.get(_detail(post))

    html = response.content.decode()
    assert "data-caption-preview" in html
    section = html.split("data-caption-preview", 1)[1]
    assert (
        "Hi \U0001f331 garden!\n\nSecond line with a dash – and quotes &quot;&lt;b&gt;&quot; &amp; more.\n" in section
    )
    assert "word " * 60 + "END" in section
    assert "Old text" not in section
    preview = response.context["caption_channels"][0]["previews"][0]
    assert preview["caption"] == LONG_CAPTION == _latest(variant).text


def test_caption_card_hides_disabled_channels_and_offers_other_enabled_ones(client, workspace, user, content):
    post, variant, instagram = content
    linkedin = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
    hidden = create_manual_channel(workspace, "tiktok", "Ella TikTok")
    set_channel_enabled(hidden, enabled=False)
    client.force_login(user)

    response = client.get(_detail(post))

    channels = response.context["caption_channels"]
    by_account = {item["account"].pk: item for item in channels}
    assert set(by_account) == {instagram.pk, linkedin.pk}  # disabled TikTok is not shown
    assert by_account[instagram.pk]["variant"] == variant
    assert by_account[linkedin.pk]["variant"] is None  # enabled after the content was created
    assert by_account[linkedin.pk]["key"] == f"new:{linkedin.pk}"
    assert by_account[linkedin.pk]["char_limit"] == linkedin.char_limit
    assert by_account[linkedin.pk]["previews"] == []  # only Instagram has a preview
    assert response.content.decode().count('<textarea id="caption-') == 2  # one field per shown channel


def test_hidden_channel_with_existing_copy_is_not_shown(client, workspace, user):
    kept = create_manual_channel(workspace, "instagram", "Ella Instagram")
    dropped = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
    post = create_content(workspace, "Two channels", user)
    set_channel_enabled(dropped, enabled=False)
    client.force_login(user)

    channels = client.get(_detail(post)).context["caption_channels"]

    assert [item["account"].pk for item in channels] == [kept.pk]
    assert PlatformPost.objects.filter(post=post, social_account=dropped).exists()  # data is kept


def test_saving_a_caption_for_a_newly_enabled_channel_creates_its_copy_and_version(client, workspace, user, content):
    post, variant, instagram = content
    linkedin = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
    client.force_login(user)

    response = client.post(
        _detail(post),
        {"action": "save_caption", "platform_post": f"new:{linkedin.pk}", "caption": "LinkedIn text \U0001f331"},
    )

    assert response.status_code == 302
    copy = PlatformPost.objects.get(post=post, social_account=linkedin)
    assert copy.status == PlatformPost.Status.DRAFT
    assert copy.platform_specific_caption == "LinkedIn text \U0001f331"
    assert _latest(copy).text == "LinkedIn text \U0001f331"


def test_new_channel_caption_respects_the_channel_character_limit(client, workspace, user, content):
    post, variant, instagram = content
    linkedin = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
    client.force_login(user)

    response = client.post(
        _detail(post),
        {"action": "save_caption", "platform_post": f"new:{linkedin.pk}", "caption": "x" * (linkedin.char_limit + 1)},
    )

    assert response.status_code == 200
    assert f"exceeds the {linkedin.char_limit}-character limit" in response.context["caption_error"]
    assert not PlatformPost.objects.filter(post=post, social_account=linkedin).exists()


def test_new_channel_copy_is_refused_once_content_left_drafting(client, workspace, user, content):
    post, variant, instagram = content
    PlatformPost.objects.filter(pk=variant.pk).update(status=PlatformPost.Status.APPROVED)
    linkedin = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
    client.force_login(user)

    response = client.post(
        _detail(post), {"action": "save_caption", "platform_post": f"new:{linkedin.pk}", "caption": "Late text"}
    )

    assert response.status_code == 200
    assert "enabled after this content left drafting" in response.context["caption_error"]
    assert not PlatformPost.objects.filter(post=post, social_account=linkedin).exists()
    variant.refresh_from_db()
    assert variant.status == PlatformPost.Status.APPROVED


def test_new_channel_save_rejects_disabled_or_foreign_channels(client, workspace, user, content):
    post, variant, instagram = content
    hidden = create_manual_channel(workspace, "tiktok", "Ella TikTok")
    set_channel_enabled(hidden, enabled=False)
    client.force_login(user)

    for target in (f"new:{hidden.pk}", "new:00000000-0000-0000-0000-000000000000"):
        response = client.post(_detail(post), {"action": "save_caption", "platform_post": target, "caption": "No"})
        assert response.status_code == 404
    assert not PlatformPost.objects.filter(post=post, social_account=hidden).exists()


def test_client_and_viewer_cannot_write_captions_or_new_channel_copies(client, workspace, user, content):
    post, variant, instagram = content
    linkedin = create_manual_channel(workspace, "linkedin_personal", "Ella LinkedIn")
    _save_caption(client, variant, "x")  # anonymous: redirected to login, nothing written
    assert not TextDocument.objects.filter(platform_post=variant).exists()
    client.force_login(user)
    _save_caption(client, variant, "Saved by the team")

    for role in ("client", "viewer"):
        member = User.objects.create_user(
            email=f"{role}-f6b02@example.test", password="testpass123", tos_accepted_at=timezone.now()
        )
        WorkspaceMembership.objects.create(user=member, workspace=workspace, workspace_role=role)
        client.force_login(member)
        page = client.get(_detail(post))
        assert page.status_code == 200
        assert b"Save version" not in page.content
        assert b"readonly" in page.content
        for target in (str(variant.pk), f"new:{linkedin.pk}"):
            denied = client.post(
                _detail(post), {"action": "save_caption", "platform_post": target, "caption": "Not allowed"}
            )
            assert denied.status_code == 403
        # Composer routes need create_posts, so these roles cannot reach the composer save either.
        assert _composer_save(client, post, [instagram]).status_code == 403

    assert not PlatformPost.objects.filter(post=post, social_account=linkedin).exists()
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Saved by the team"
