from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.jrc_channels.models import ChannelProfile
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.jrc_channels.signals import PROTECTED_FIELDS
from apps.social_accounts.models import PlatformVisibility, SocialAccount

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "platform", ["instagram", "facebook", "linkedin_personal", "linkedin_company", "tiktok", "youtube", "custom"]
)
def test_create_without_oauth_or_backfill(workspace, platform, django_capture_on_commit_callbacks):
    with (
        patch("apps.analytics.tasks.backfill_account_analytics") as backfill,
        django_capture_on_commit_callbacks(execute=True),
    ):
        account = create_manual_channel(workspace, platform, "Forendors" if platform == "custom" else "Ella", "ella")
    backfill.assert_not_called()
    account.refresh_from_db()
    assert account.account_platform_id.startswith("manual:")
    assert account.connection_status == "connected"
    assert account.account_handle == "ella"
    assert account.jrc_channel.is_enabled
    assert account.jrc_channel.kind == ("custom" if platform == "custom" else "manual")
    assert not account.oauth_access_token
    assert not account.oauth_refresh_token
    assert not account.oauth_facebook_user_access_token
    assert account.token_expires_at is None
    assert account.facebook_user_token_expires_at is None
    assert account.char_limit == (5000 if platform == "youtube" else account.PLATFORM_CHAR_LIMITS.get(platform, 2200))


def test_duplicate_platforms_and_workspace_isolation(workspace, organization):
    from apps.workspaces.models import Workspace

    other = Workspace.objects.create(organization=organization, name="Other client")
    first = create_manual_channel(workspace, "instagram", "Ella")
    second = create_manual_channel(workspace, "instagram", "Ella second")
    third = create_manual_channel(other, "instagram", "Other")
    assert len({first.account_platform_id, second.account_platform_id, third.account_platform_id}) == 3
    assert set(SocialAccount.objects.filter(workspace=workspace)) == {first, second}


def test_profile_metadata_and_cascade(workspace):
    account = create_manual_channel(
        workspace, "custom", "Ella", display_name="Forendors", profile_url="https://example.invalid/ella"
    )
    assert str(account.jrc_channel) == "Forendors"
    assert account.jrc_channel.profile_url == "https://example.invalid/ella"
    assert account.char_limit == 2200
    with pytest.raises(IntegrityError), transaction.atomic():
        ChannelProfile.objects.create(social_account=account)
    account.delete()
    assert not ChannelProfile.objects.exists()


@pytest.mark.parametrize("kwargs", [{"platform": "unknown"}, {"name": ""}, {"profile_url": "not a URL"}])
def test_invalid_channel_rolls_back_entire_creation(workspace, kwargs):
    values = {"platform": "instagram", "name": "Ella", **kwargs}
    with pytest.raises(ValidationError):
        create_manual_channel(workspace, **values)
    assert not SocialAccount.objects.exists()
    assert not ChannelProfile.objects.exists()


@pytest.mark.parametrize(
    "platform", ["mastodon", "bluesky", "pinterest", "threads", "google_business", "devto", "instagram_login"]
)
def test_unsupported_manual_platform_is_rejected_before_save(workspace, platform):
    with (
        patch.object(SocialAccount, "save", side_effect=AssertionError("Unsupported account must never be saved")),
        pytest.raises(ValidationError) as error,
    ):
        create_manual_channel(workspace, platform, "Ella")
    assert "platform" in error.value.message_dict
    assert not SocialAccount.objects.exists()
    assert not ChannelProfile.objects.exists()


@pytest.mark.parametrize("platform", ["instagram", "custom"])
@pytest.mark.parametrize("partial", [True, False])
def test_upstream_save_cannot_change_status_or_tokens(workspace, platform, partial):
    account = create_manual_channel(workspace, platform, "Ella")
    original = {field: getattr(account, field) for field in PROTECTED_FIELDS}
    account.connection_status = "error"
    account.last_error = "Please reconnect"
    account.oauth_access_token = "fake-token"
    account.oauth_refresh_token = "fake-refresh"
    account.oauth_facebook_user_access_token = "fake-user-token"
    account.token_expires_at = account.facebook_user_token_expires_at = timezone.now() + timedelta(hours=1)
    account.account_name = "Renamed"
    account.save(update_fields=[*PROTECTED_FIELDS, "account_name"] if partial else None)
    account.refresh_from_db()
    assert {field: getattr(account, field) for field in PROTECTED_FIELDS} == original
    assert account.account_name == "Renamed"


@pytest.mark.parametrize("platform", ["instagram", "custom"])
def test_toggle_controls_upstream_visibility_and_preserves_posts(workspace, platform, user):
    from apps.composer.models import PlatformPost, Post

    account = create_manual_channel(workspace, platform, "Ella")
    # Populate the relation cache, as an open client profile page would.
    assert account.jrc_channel.is_enabled
    post = Post.objects.create(workspace=workspace, author=user)
    pp = PlatformPost.objects.create(post=post, social_account=account)
    set_channel_enabled(account, enabled=False)
    assert not account.jrc_channel.is_enabled
    assert not SocialAccount.objects.filter(workspace=workspace, connection_status="connected").exists()
    # A routine health write must not reconnect a deliberately disabled channel.
    account.connection_status = "connected"
    account.save(update_fields=["connection_status"])
    account.refresh_from_db()
    assert account.connection_status == "disconnected"
    set_channel_enabled(account, enabled=True)
    assert account.jrc_channel.is_enabled
    assert SocialAccount.objects.filter(workspace=workspace, connection_status="connected").get() == account
    assert PlatformPost.objects.get(pk=pp.pk).social_account_id == account.pk


def test_nonlocal_account_can_still_change_status(workspace):
    account = SocialAccount(
        workspace=workspace, platform="facebook", account_platform_id="fake", account_name="Upstream"
    )
    account._skip_analytics_backfill = True
    account.save()
    account.connection_status = "error"
    account.save(update_fields=["connection_status"])
    account.refresh_from_db()
    assert account.connection_status == "error"


def test_custom_cannot_be_connected_with_oauth():
    from providers import PROVIDER_REGISTRY

    assert "custom" not in PROVIDER_REGISTRY
    # This row comes from the data migration, not a fixture.
    assert PlatformVisibility.objects.get(platform="custom").is_visible is False
    assert "custom" not in dict(PlatformVisibility.visible_choices())
