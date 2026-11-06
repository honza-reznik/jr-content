import json
from apps.accounts.models import User
from apps.api_keys import services
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace
seed = json.load(open('/jrc-scripts/../ops/test-output/P1-B29/seed.json')) if False else None
owner = User.objects.get(email__startswith='p1b29-owner-')
ws = Workspace.objects.get(name__startswith='P1-B29 Vlastni')
acc = SocialAccount.objects.get(workspace=ws)
issued = services.issue_api_key(workspace=ws, social_accounts=[acc], issued_by=owner, name='B29 klic', permissions=['create_posts'])
print('TOKEN=' + issued.plaintext_token)
