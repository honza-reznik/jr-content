import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_texts.models import TextDocument
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def instagram_content(workspace, user):
    account = create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Garden reel", user)
    variant = PlatformPost.objects.get(post=post, social_account=account)
    return post, variant


def _url(workspace, post):
    return reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})


def _save(client, variant, caption, *, base_version=""):
    return client.post(
        f"{_url(variant.post.workspace, variant.post)}?tab=caption",
        {
            "action": "save_caption",
            "platform_post": str(variant.pk),
            "base_version": str(base_version),
            "caption": caption,
        },
    )


def test_caption_editor_shows_instagram_limit_and_counts_unicode_emoji(client, workspace, user, instagram_content):
    post, variant = instagram_content
    client.force_login(user)

    response = client.get(f"{_url(workspace, post)}?tab=caption")

    assert response.status_code == 200
    channel = response.context["caption_channels"][0]
    assert channel["char_count"] == variant.social_account.caption_wire_length(channel["text"])
    assert channel["char_limit"] == 2200
    assert variant.social_account.caption_wire_length("Hi \U0001f331") == len("Hi \U0001f331") == 4


def test_caption_save_creates_plain_version_and_copies_to_channel(client, user, instagram_content):
    post, variant = instagram_content
    client.force_login(user)

    response = _save(client, variant, "Hi \U0001f331")

    assert response.status_code == 302
    variant.refresh_from_db()
    document = TextDocument.objects.get(platform_post=variant, kind=TextDocument.Kind.CAPTION)
    saved = document.versions.filter(state="final").order_by("-number").first()
    assert document.format == TextDocument.Format.PLAIN
    assert saved.text == "Hi \U0001f331"
    assert saved.char_count is None  # Length is platform-specific and computed with the account's wire rules.
    assert variant.platform_specific_caption == "Hi \U0001f331"


def test_instagram_limit_is_enforced_without_creating_a_version(client, user, instagram_content):
    post, variant = instagram_content
    client.force_login(user)

    response = _save(client, variant, "Hi \U0001f331" + "x" * 2201)

    assert response.status_code == 200
    assert b"exceeds the 2200-character limit" in response.content
    assert response.context["caption_channels"][0]["char_count"] == 2205
    assert not TextDocument.objects.filter(platform_post=variant).exists()
    variant.refresh_from_db()
    assert variant.platform_specific_caption is None


def test_caption_newlines_are_normalized_before_exact_limit_check_and_save(client, user, instagram_content):
    post, variant = instagram_content
    client.force_login(user)
    caption = "x\r\n" * 1100  # 2,200 characters after CRLF becomes LF.

    response = _save(client, variant, caption)

    assert response.status_code == 302
    variant.refresh_from_db()
    document = TextDocument.objects.get(platform_post=variant, kind=TextDocument.Kind.CAPTION)
    saved = document.versions.filter(state="final").order_by("-number").first()
    normalized = caption.replace("\r\n", "\n")
    assert len(normalized) == 2200
    assert "\r" not in saved.text
    assert saved.text == normalized
    assert variant.platform_specific_caption == normalized


def test_caption_versions_compare_words(client, user, instagram_content):
    post, variant = instagram_content
    client.force_login(user)
    _save(client, variant, "Hello old garden")
    latest = TextDocument.objects.get(platform_post=variant).versions.filter(state="final").order_by("-number").first()

    response = _save(client, variant, "Hello new garden", base_version=latest.pk)

    assert response.status_code == 302
    page = client.get(f"{_url(variant.post.workspace, post)}?tab=caption")
    assert b"Compare with version 2" in page.content
    assert b"<del" in page.content and b"old" in page.content
    assert b"<ins" in page.content and b"new" in page.content


def test_stale_caption_form_conflicts_and_client_cannot_write(client, workspace, user, instagram_content):
    post, variant = instagram_content
    client.force_login(user)
    _save(client, variant, "First version")
    latest = TextDocument.objects.get(platform_post=variant).versions.filter(state="final").order_by("-number").first()
    _save(client, variant, "Second version", base_version=latest.pk)

    stale = _save(client, variant, "Stale overwrite", base_version=latest.pk)
    assert stale.status_code == 200
    assert "no longer current" in stale.context["caption_error"]
    latest.refresh_from_db()
    assert latest.text == "First version"

    client_user = User.objects.create_user(
        email="caption-client@example.test", password="testpass123", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(user=client_user, workspace=workspace, workspace_role="client")
    client.force_login(client_user)
    denied = _save(client, variant, "Client overwrite", base_version=latest.pk)
    assert denied.status_code == 403
