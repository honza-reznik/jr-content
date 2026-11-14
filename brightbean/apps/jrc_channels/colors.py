"""Channel accents share workspace validation and contrast calculation."""

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from apps.workspaces.colors import PALETTE, validate_accent

PLATFORM_COLORS = {
    "instagram": "#E4405F",
    "instagram_login": "#E4405F",
    "tiktok": "#000000",
    "youtube": "#FF0000",
    "facebook": "#1877F2",
    "linkedin_personal": "#0A66C2",
    "linkedin_company": "#0A66C2",
}


def channel_accent(account):
    profile = getattr(account, "jrc_channel", None)
    if profile and profile.color:
        try:
            return validate_accent(profile.color)
        except ValidationError:
            pass
    if account.platform in PLATFORM_COLORS:
        return PLATFORM_COLORS[account.platform]
    if profile and account.platform == "custom":
        host = urlsplit(profile.profile_url).hostname or ""
        if (
            host == "forendors.cz"
            or host.endswith(".forendors.cz")
            or "forendors" in (profile.display_name or account.account_name).casefold()
        ):
            return "#047857"
    return PALETTE[account.pk.int % len(PALETTE)][0]
