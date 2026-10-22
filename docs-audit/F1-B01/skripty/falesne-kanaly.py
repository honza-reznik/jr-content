# Vytvoří vymyšlené "připojené" kanály pro workspace Ella, aby šlo vyzkoušet composer bez OAuth.
# Spuštění: docker exec -i jrc-audit-brightbean-app-1 python manage.py shell < falesne-kanaly.py
# Bez tokenů; worker běží ve vnitřní síti bez internetu, takže se nic neodešle ven.
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

ws = Workspace.objects.get(name="Ella")
for platform, name, handle in [
    ("instagram", "Ella (test)", "ella.test"),
    ("linkedin_company", "Ella s.r.o. (test)", "ella-test"),
]:
    acc, created = SocialAccount.objects.get_or_create(
        workspace=ws, platform=platform, account_platform_id=f"fake-{platform}",
        defaults={"account_name": name, "account_handle": handle},
    )
    print(platform, acc.id, "vytvořen" if created else "už existoval")
