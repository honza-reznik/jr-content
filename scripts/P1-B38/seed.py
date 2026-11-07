"""Vymyšlená data a relace pouze v odděleném projektu jrc-p1b38 (P1-B38): přejmenování, smazání, odmítnuté stránky.
Relace se vypisují jen do ignorovaného souboru (ops/test-output)."""
import json
import os
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

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13803'
tag = uuid4().hex[:6]
org = Organization.objects.create(name=f'P1-B38 {tag}')
users, sessions = {}, {}
for role in ('owner', 'editor', 'client'):
    user = User.objects.create(
        email=f'p1b38-{role}-{uuid4().hex[:8]}@example.invalid', name=f'B38 {role}', tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'owner' else 'member')
    users[role] = user
result = {'tag': tag, 'workspaces': {}, 'sessions': sessions}
for mode in ('required_internal_and_client', 'optional'):
    ws = Workspace.objects.create(organization=org, name=f'P1-B38 {mode} {tag}', approval_workflow_mode=mode)
    instagram = create_manual_channel(ws, 'custom', 'P1-B38 Instagram')
    for role, user in users.items():
        WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=role)
    def make(title):
        post = Post.objects.create(workspace=ws, author=users['owner'], title=title, caption='Schválený popisek')
        copy = PlatformPost.objects.create(post=post, social_account=instagram, status='approved')
        ApprovalAction.objects.create(post=post, user=users['owner'], action='approved')
        return post, copy
    post, copy = make(f'B38 {mode} rename')
    doomed, _ = make(f'B38 {mode} doomed')
    result['workspaces'][mode] = {'id': str(ws.pk), 'post': str(post.pk), 'copy': str(copy.pk), 'doomed': str(doomed.pk)}
for role, user in users.items():
    user.last_workspace_id = Workspace.objects.get(organization=org, name=f'P1-B38 required_internal_and_client {tag}').pk
    user.save(update_fields=['last_workspace_id'])
    client = Client()
    client.force_login(user)
    sessions[role] = client.cookies['sessionid'].value
# Cizí klient: workspace, kde nikdo z nich není členem.
other = Workspace.objects.create(organization=org, name=f'P1-B38 cizí {tag}')
result['foreign'] = str(other.pk)
print('SEED=' + json.dumps(result))
