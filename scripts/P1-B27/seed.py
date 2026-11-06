"""Vymyšlená data a relace pouze v odděleném projektu jrc-p1b27 (P1-B27): 4 režimy schvalování × 6 rolí."""
import json
import os
from uuid import uuid4

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13693'
ROLES = ('owner', 'manager', 'editor', 'contributor', 'client', 'viewer')
MODES = ('none', 'optional', 'required_internal', 'required_internal_and_client')
org = Organization.objects.create(name=f'P1-B27 Modes {uuid4().hex[:6]}')
users, sessions = {}, {}
for role in ROLES:
    user = User.objects.create(
        email=f'p1b27-{role}-{uuid4().hex[:8]}@example.invalid', name=f'Modes {role}', tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'owner' else 'member')
    users[role] = user
result = {'workspaces': {}, 'sessions': sessions}
for mode in MODES:
    ws = Workspace.objects.create(organization=org, name=f'P1-B27 {mode}', approval_workflow_mode=mode)
    account = create_manual_channel(ws, 'instagram', f'P1-B27 {mode} channel')
    for role in ROLES:
        WorkspaceMembership.objects.create(user=users[role], workspace=ws, workspace_role=role)
    posts = {}
    cases = [('draft_caption', 'draft', 'Ready caption'), ('draft_empty', 'draft', ''), ('review', 'pending_review', 'Ready caption')]
    if mode == 'required_internal_and_client':
        cases.append(('client_step', 'pending_client', 'Ready caption'))
    for name, status, caption in cases:
        post = Post.objects.create(workspace=ws, author=users['owner'], title=f'P1-B27 {mode} {name}', caption=caption)
        pp = PlatformPost.objects.create(post=post, social_account=account, status=status)
        posts[name] = {'post': str(post.pk), 'platform_post': str(pp.pk)}
    result['workspaces'][mode] = {'id': str(ws.pk), 'account': str(account.pk), 'posts': posts}
for role in ROLES:
    users[role].last_workspace_id = Workspace.objects.get(organization=org, name='P1-B27 none').pk
    users[role].save(update_fields=['last_workspace_id'])
    client = Client()
    client.force_login(users[role])
    sessions[role] = client.cookies['sessionid'].value
# Session data are emitted only to an ignored file by the caller.
print('SEED=' + json.dumps(result))
