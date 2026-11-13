"""Vymyšlená data pro skutečnou obnovu P2-B01; přístupové údaje jen do ignorovaného výstupu."""
import json
import secrets
from pathlib import Path

from allauth.account.models import EmailAddress
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.members.models import Invitation, OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

credentials = dict(line.split('=', 1) for line in Path('/sprint-ucty.env').read_text().splitlines() if line and not line.startswith('#') and '=' in line)
org = Organization.objects.create(name='P2-B01 testovací organizace')
ws = Workspace.objects.create(organization=org, name='P2-B01 testovací klient')
fixture = {'workspace_id': str(ws.pk)}
for role in ('OWNER', 'CLIENT'):
    email = credentials[f'SPRINT_{role}_EMAIL'].strip().strip('"\'')
    password = credentials[f'SPRINT_{role}_PASSWORD'].strip().strip('"\'')
    user = User.objects.create_user(email=email, password=password, name=f'Test {role}', tos_accepted_at=timezone.now())
    EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
    OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'OWNER' else 'member')
    WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=role.lower())
    fixture[role.lower()] = {'id': str(user.pk), 'email': email, 'password': password}
real = User.objects.create_user(email='restored-person@example.org', password=secrets.token_urlsafe(20), name='Restored test person', tos_accepted_at=timezone.now())
EmailAddress.objects.create(user=real, email=real.email, primary=True, verified=True)
EmailAddress.objects.create(user=real, email='secondary-person@example.org', verified=False)
OrgMembership.objects.create(user=real, organization=org, org_role='member')
WorkspaceMembership.objects.create(user=real, workspace=ws, workspace_role='viewer')
Invitation.objects.create(organization=org, email='invite-person@example.org', invited_by=user, expires_at=timezone.now() + timezone.timedelta(days=1))
fixture['real_id'] = str(real.pk)
fixture['real_password_hash'] = real.password
create_manual_channel(ws, 'instagram', 'Testovací Instagram')
owner = User.objects.get(pk=fixture['owner']['id'])
post = create_content(ws, 'Testovací obsah po bezpečné obnově', owner)
fixture['post_id'] = str(post.pk)
print(json.dumps(fixture))
