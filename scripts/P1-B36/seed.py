"""Vymyšlená data a relace pouze v odděleném projektu jrc-p1b36 (P1-B36): úprava popisku po schválení,
import TSV, stažení z plánu. Relace se vypisují jen do ignorovaného souboru (ops/test-output)."""
import json
import os
from datetime import timedelta
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

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13783'
tag = uuid4().hex[:6]
org = Organization.objects.create(name=f'P1-B36 {tag}')
users, sessions = {}, {}
for role in ('owner', 'editor'):
    user = User.objects.create(
        email=f'p1b36-{role}-{uuid4().hex[:8]}@example.invalid', name=f'B36 {role}', tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'owner' else 'member')
    users[role] = user
result = {'tag': tag, 'workspaces': {}, 'sessions': sessions}
for mode in ('required_internal_and_client', 'optional'):
    ws = Workspace.objects.create(organization=org, name=f'P1-B36 {mode} {tag}', approval_workflow_mode=mode)
    instagram = create_manual_channel(ws, 'custom', 'P1-B36 Instagram')
    tiktok = create_manual_channel(ws, 'custom', 'P1-B36 TikTok')
    for role, user in users.items():
        WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=role)
    post = Post.objects.create(workspace=ws, author=users['owner'], title=f'B36 {mode} approved', caption='Schválený popisek')
    edited = PlatformPost.objects.create(post=post, social_account=instagram, status='approved')
    other = PlatformPost.objects.create(post=post, social_account=tiktok, status='approved')
    ApprovalAction.objects.create(post=post, user=users['owner'], action='approved')
    when = timezone.now() + timedelta(days=2)
    planned = Post.objects.create(workspace=ws, author=users['owner'], title=f'B36 {mode} planned', caption='Plán', scheduled_at=when)
    planned_pp = PlatformPost.objects.create(post=planned, social_account=instagram, status='scheduled', scheduled_at=when)
    result['workspaces'][mode] = {
        'id': str(ws.pk), 'post': str(post.pk), 'edited': str(edited.pk), 'other': str(other.pk),
        'planned': str(planned_pp.pk),
    }
for role, user in users.items():
    user.last_workspace_id = Workspace.objects.get(organization=org, name=f'P1-B36 required_internal_and_client {tag}').pk
    user.save(update_fields=['last_workspace_id'])
    client = Client()
    client.force_login(user)
    sessions[role] = client.cookies['sessionid'].value
print('SEED=' + json.dumps(result))
