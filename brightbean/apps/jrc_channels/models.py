from django.db import models


class ChannelProfile(models.Model):
    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual"
        CUSTOM = "custom", "Custom"

    social_account = models.OneToOneField(
        "social_accounts.SocialAccount",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="jrc_channel",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MANUAL)
    display_name = models.CharField(max_length=100, blank=True, default="")
    is_enabled = models.BooleanField(default=True)
    profile_url = models.URLField(max_length=2000, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name or self.social_account.get_platform_display()
