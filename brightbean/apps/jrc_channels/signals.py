from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.social_accounts.models import SocialAccount

from .models import ChannelProfile

PROTECTED_FIELDS = (
    "connection_status",
    "last_error",
    "oauth_access_token",
    "oauth_refresh_token",
    "token_expires_at",
    "oauth_facebook_user_access_token",
    "facebook_user_token_expires_at",
)


@receiver(pre_save, sender=SocialAccount)
def preserve_local_channel_state(sender, instance, using, raw=False, **kwargs):
    """Ignore upstream connection/token writes to local channels.

    Local services may explicitly change status; bulk QuerySet.update() bypasses
    Django signals and must never be used to change a local channel's status.
    """
    if raw or getattr(instance, "_jrc_allow_status_change", False):
        return
    if (
        not ChannelProfile.objects.using(using)
        .filter(social_account_id=instance.pk, kind__in=[ChannelProfile.Kind.MANUAL, ChannelProfile.Kind.CUSTOM])
        .exists()
    ):
        return
    previous = sender.objects.using(using).filter(pk=instance.pk).values(*PROTECTED_FIELDS).first()
    if previous:
        for field, value in previous.items():
            setattr(instance, field, value)
