"""Vymyšlená data a relace pouze v odděleném projektu jrc-p1b32 (P1-B32): plánování podle práv, Review podle režimu."""
import json
import os
from datetime import time
from uuid import uuid4

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.calendar.models import PostingSlot, Queue
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13743'
ROLES = ('owner', 'manager', 'editor', 'contributor')
MODES = ('none', 'optional', 'required_internal')
tag = uuid4().hex[:6]
org = Organization.objects.create(name=f'P1-B32 Planning {tag}')
users, sessions = {}, {}
for role in ROLES:
    user = User.objects.create(
        email=f'p1b32-{role}-{uuid4().hex[:8]}@example.invalid', name=f'Plan {role}', tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'owner' else 'member')
    users[role] = user
result = {'workspaces': {}, 'sessions': sessions, 'tag': tag}
for mode in MODES:
    ws = Workspace.objects.create(organization=org, name=f'P1-B32 {mode} {tag}', approval_workflow_mode=mode)
    account = create_manual_channel(ws, 'custom', f'P1-B32 {mode} channel')
    for day in range(7):
        PostingSlot.objects.create(social_account=account, day_of_week=day, time=time(9, 0))
    Queue.objects.create(workspace=ws, name='P1-B32 queue', social_account=account)
    for role in ROLES:
        WorkspaceMembership.objects.create(user=users[role], workspace=ws, workspace_role=role)
    posts = {}
    cases = [(f'{role}_draft', role, 'draft') for role in ROLES]
    cases += [('owner_on_hold', 'owner', 'on_hold'), ('owner_approved', 'owner', 'approved')]
    for name, role, status in cases:
        post = Post.objects.create(workspace=ws, author=users[role], title=f'P1-B32 {mode} {name}', caption='Ready caption')
        pp = PlatformPost.objects.create(post=post, social_account=account, status=status)
        posts[name] = {'post': str(post.pk), 'platform_post': str(pp.pk)}
    result['workspaces'][mode] = {'id': str(ws.pk), 'account': str(account.pk), 'posts': posts}
for role in ROLES:
    users[role].last_workspace_id = Workspace.objects.get(organization=org, name=f'P1-B32 none {tag}').pk
    users[role].save(update_fields=['last_workspace_id'])
    client = Client()
    client.force_login(users[role])
    sessions[role] = client.cookies['sessionid'].value
# Session data are emitted only to an ignored file by the caller.
print('SEED=' + json.dumps(result))
