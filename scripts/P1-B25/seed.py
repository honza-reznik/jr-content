"""Vymyšlená data a relace pouze v odděleném projektu P1-B25."""
import json
import os
from uuid import uuid4

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post, PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13673'
org = Organization.objects.create(name='P1-B25 Audit')
ws = Workspace.objects.create(organization=org, name='P1-B25 Studio')
account = create_manual_channel(ws, 'instagram', 'P1-B25 test channel')
users, sessions = {}, {}
for role in ('owner', 'manager', 'editor', 'contributor', 'client', 'viewer', 'nonmember'):
    user = User.objects.create(email=f'p1b25-{role}-{uuid4().hex[:8]}@example.invalid', name=f'Audit {role}', tos_accepted_at=timezone.now())
    if role != 'nonmember':
        OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'owner' else 'member')
        WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=role)
        user.last_workspace_id = ws.pk
        user.save(update_fields=['last_workspace_id'])
    client = Client()
    client.force_login(user)
    sessions[role] = client.cookies['sessionid'].value
    users[role] = user
posts = {}
for role in ('client', 'viewer'):
    posts[role] = {}
    for action in ('delete', 'channel'):
        post = Post.objects.create(workspace=ws, author=users['owner'], title=f'P1-B25 {role} {action}')
        PlatformPost.objects.create(post=post, social_account=account)
        posts[role][action] = str(post.pk)
result = {'workspace': str(ws.pk), 'account': str(account.pk), 'posts': posts, 'sessions': sessions}
# Session data are emitted only to an ignored file by the caller.
print('SEED=' + json.dumps(result))
