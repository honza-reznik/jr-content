from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import User
from apps.api_keys import services
from apps.api_keys.models import ApiKey
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace
owner = User.objects.get(email__startswith='p1b29-owner-')
ws = Workspace.objects.get(name__startswith='P1-B29 Vlastni')
k = services.issue_api_key(workspace=ws, social_accounts=[SocialAccount.objects.get(workspace=ws)], issued_by=owner, name='B29 prosly', permissions=['create_posts'], expires_at=timezone.now() + timedelta(days=1)).api_key
ApiKey.objects.filter(pk=k.pk).update(expires_at=timezone.now() - timedelta(days=2))
print('created expired')
