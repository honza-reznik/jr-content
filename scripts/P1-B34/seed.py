"""Vymyšlená data a relace pouze v odděleném projektu jrc-p1b34 (P1-B34): CSV import, úprava schváleného obsahu, kalendář."""
import json
import os
from datetime import datetime, timedelta, time
from uuid import uuid4

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13763'
ROLES = ('owner', 'manager', 'editor', 'contributor')
MODES = ('none', 'optional', 'required_internal', 'required_internal_and_client')
tag = uuid4().hex[:6]
org = Organization.objects.create(name=f'P1-B34 {tag}')
users, sessions = {}, {}
for role in ROLES:
    user = User.objects.create(
        email=f'p1b34-{role}-{uuid4().hex[:8]}@example.invalid', name=f'B34 {role}', tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'owner' else 'member')
    users[role] = user
# Den v aktuálním měsíci (kalendář otevírá aktuální měsíc), ne dnes, s volným dnem vedle pro přetažení.
today = timezone.localdate()
day = today + timedelta(days=2) if today.day <= 20 else today - timedelta(days=4)
result = {'workspaces': {}, 'sessions': sessions, 'tag': tag, 'day': day.isoformat()}
for mode in MODES:
    ws = Workspace.objects.create(organization=org, name=f'P1-B34 {mode} {tag}', approval_workflow_mode=mode)
    account = create_manual_channel(ws, 'custom', f'P1-B34 {mode} channel')
    for role in ROLES:
        WorkspaceMembership.objects.create(user=users[role], workspace=ws, workspace_role=role)
    posts = {}
    cases = [
        ('owner_approved_edit', 'owner', 'approved'),
        ('owner_approved_cal', 'owner', 'approved'),
        ('owner_draft_cal', 'owner', 'draft'),
        ('editor_approved', 'editor', 'approved'),
        ('editor_pending', 'editor', 'pending_review'),
        ('editor_draft', 'editor', 'draft'),
    ]
    for hour, (name, role, status) in enumerate(cases, start=9):
        when = timezone.make_aware(datetime.combine(day, time(hour, 0)))
        post = Post.objects.create(
            workspace=ws, author=users[role], title=f'B34 {mode} {name}', caption='Ready caption', scheduled_at=when
        )
        pp = PlatformPost.objects.create(post=post, social_account=account, status=status, scheduled_at=when)
        if status == 'approved':
            ApprovalAction.objects.create(post=post, user=users['owner'], action='approved')
        posts[name] = {'post': str(post.pk), 'platform_post': str(pp.pk)}
    result['workspaces'][mode] = {'id': str(ws.pk), 'account': str(account.pk), 'posts': posts}
for role in ROLES:
    users[role].last_workspace_id = Workspace.objects.get(organization=org, name=f'P1-B34 none {tag}').pk
    users[role].save(update_fields=['last_workspace_id'])
    client = Client()
    client.force_login(users[role])
    sessions[role] = client.cookies['sessionid'].value
# Session data are emitted only to an ignored file by the caller.
print('SEED=' + json.dumps(result))
