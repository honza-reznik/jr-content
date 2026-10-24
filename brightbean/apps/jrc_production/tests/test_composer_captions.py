"""F6-B02: composer and previews show the latest saved caption read-only; more channels on the Caption card."""

import json
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
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
