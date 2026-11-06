"""Vymyšlená data a relace pouze v odděleném projektu P1-B29."""
import json
import os
from uuid import uuid4

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13713'
sfx = uuid4().hex[:6]
org = Organization.objects.create(name=f'P1-B29 Org {sfx}')
ws_a = Workspace.objects.create(organization=org, name=f'P1-B29 Vlastni {sfx}')
ws_b = Workspace.objects.create(organization=org, name=f'P1-B29 Cizi {sfx}')
ch_a = create_manual_channel(ws_a, 'instagram', f'P1-B29 kanal vlastni {sfx}')
ch_b = create_manual_channel(ws_b, 'instagram', f'P1-B29 kanal CIZI {sfx}')
owner = User.objects.create(email=f'p1b29-owner-{sfx}@example.invalid', name='B29 owner', tos_accepted_at=timezone.now())
OrgMembership.objects.create(user=owner, organization=org, org_role='owner')
WorkspaceMembership.objects.create(user=owner, workspace=ws_a, workspace_role='owner')
owner.last_workspace_id = ws_a.pk
owner.save(update_fields=['last_workspace_id'])
other = User.objects.create(email=f'p1b29-other-{sfx}@example.invalid', name='B29 other', tos_accepted_at=timezone.now())
OrgMembership.objects.create(user=other, organization=org, org_role='member')
WorkspaceMembership.objects.create(user=other, workspace=ws_b, workspace_role='owner')
client = Client()
client.force_login(owner)
print('SEED=' + json.dumps({'org': str(org.pk), 'ws_a': str(ws_a.pk), 'ws_b': str(ws_b.pk), 'ch_a': str(ch_a.pk),
      'ch_b': str(ch_b.pk), 'ws_b_name': ws_b.name, 'ch_b_name': ch_b.account_name,
      'session': client.cookies['sessionid'].value}))
