from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.social_accounts.models import SocialAccount

from .models import ChannelProfile

MANUAL_PLATFORMS = frozenset(
    {"instagram", "facebook", "linkedin_personal", "linkedin_company", "tiktok", "youtube", "custom"}
)


@transaction.atomic
def create_manual_channel(workspace, platform, name, handle="", *, display_name="", profile_url=""):
    """Create a local channel without OAuth, credentials or analytics backfill."""
    if platform not in MANUAL_PLATFORMS:
        raise ValidationError({"platform": "This platform is not supported for manual channels"})
    account = SocialAccount(
        workspace=workspace,
        platform=platform,
        account_platform_id=f"manual:{uuid4()}",
        account_name=name,
        account_handle=handle,
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )
    account._skip_analytics_backfill = True
    account.full_clean()
    account.save()
    profile = ChannelProfile(
        social_account=account,
        kind=ChannelProfile.Kind.CUSTOM if platform == "custom" else ChannelProfile.Kind.MANUAL,
        display_name=display_name,
        profile_url=profile_url,
    )
    profile.full_clean()
    profile.save()
    return account


@transaction.atomic
def set_channel_enabled(account, *, enabled):
    """Keep local visibility and the upstream connected filter in agreement."""
    locked = SocialAccount.objects.select_for_update().get(pk=account.pk)
    profile = ChannelProfile.objects.select_for_update().get(social_account=locked)
    profile.is_enabled = enabled
    profile.save(update_fields=["is_enabled", "updated_at"])
    locked.connection_status = (
        SocialAccount.ConnectionStatus.CONNECTED if enabled else SocialAccount.ConnectionStatus.DISCONNECTED
    )
    locked.last_error = ""
    locked._jrc_allow_status_change = True
    try:
        locked.save(update_fields=["connection_status", "last_error", "updated_at"])
    finally:
        locked._jrc_allow_status_change = False
    account.connection_status = locked.connection_status
    account.last_error = locked.last_error
    # Invalidate a possibly cached reverse relation on the caller.
    account._state.fields_cache.pop("jrc_channel", None)
    return account
