"""Only remove channels created by check-profile.cjs in this disposable demo workspace."""
from apps.jrc_channels.management.commands.jrc_seed_demo import DEMO_WORKSPACE_ID
from apps.social_accounts.models import SocialAccount

SocialAccount.objects.filter(
    workspace_id=DEMO_WORKSPACE_ID,
    account_name__in=["Newsletter 1440", "Newsletter 390"],
    account_platform_id__startswith="manual:",
).delete()
print("Odstraněny jen pomocné kanály vizuálního testu v místním demu.")
