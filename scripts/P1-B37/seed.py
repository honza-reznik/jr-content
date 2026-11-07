"""Vymyšlená data a relace pouze v odděleném projektu jrc-p1b37 (P1-B37): úprava naplánovaného a na klienta
čekajícího obsahu (AK1), nová Final verze videa a nový náhled po schválení (AK2), vše v režimu
"Internal review + client approval". Relace se vypisují jen do ignorovaného souboru (ops/test-output)."""
import json
import os
from datetime import timedelta
from uuid import uuid4

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.calendar.models import Queue, QueueEntry
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import PostProduction
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13793'
tag = uuid4().hex[:6]
org = Organization.objects.create(name=f'P1-B37 {tag}')
ws = Workspace.objects.create(organization=org, name=f'P1-B37 klient {tag}', approval_workflow_mode='required_internal_and_client')
users, sessions = {}, {}
for role in ('owner', 'client'):
    user = User.objects.create(
        email=f'p1b37-{role}-{uuid4().hex[:8]}@example.invalid', name=f'B37 {role}', tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=user, organization=org, org_role='owner' if role == 'owner' else 'member')
    WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=role)
    users[role] = user
instagram = create_manual_channel(ws, 'custom', 'B37 Instagram')
tiktok = create_manual_channel(ws, 'custom', 'B37 TikTok')
owner = users['owner']
when = (timezone.now() + timedelta(days=5)).replace(second=0, microsecond=0)


def content(title, status, accounts, *, planned=False):
    post = Post.objects.create(workspace=ws, author=owner, title=f'B37 {title}', caption='Schválený popisek',
                               scheduled_at=when if planned else None)
    PostProduction.objects.create(post=post, stage_changed_by=owner)
    copies = [PlatformPost.objects.create(post=post, social_account=a, status=status,
                                          scheduled_at=when if planned else None) for a in accounts]
    return post, copies


planned, (planned_ig, planned_tt) = content('planned', 'scheduled', [instagram, tiktok], planned=True)
queue = Queue.objects.create(workspace=ws, social_account=instagram, name='B37 queue')
QueueEntry.objects.create(queue=queue, post=planned, position=0, assigned_slot_datetime=when)
client_wait, (client_ig,) = content('waiting for client', 'pending_client', [instagram])
composer, (composer_ig,) = content('composer planned', 'scheduled', [instagram], planned=True)
thumb, (thumb_ig,) = content('thumbnail planned', 'scheduled', [instagram], planned=True)
video, (video_ig,) = content('video', 'pending_review', [instagram])
sync_workspace(ws.pk)
for role, user in users.items():
    user.last_workspace_id = ws.pk
    user.save(update_fields=['last_workspace_id'])
    browser = Client()
    browser.force_login(user)
    sessions[role] = browser.cookies['sessionid'].value
print('SEED=' + json.dumps({
    'tag': tag, 'workspace': str(ws.pk), 'when': when.isoformat(), 'sessions': sessions,
    'planned': {'post': str(planned.pk), 'ig': str(planned_ig.pk), 'tt': str(planned_tt.pk)},
    'client_wait': {'post': str(client_wait.pk), 'ig': str(client_ig.pk)},
    'composer': {'post': str(composer.pk), 'ig': str(composer_ig.pk)},
    'thumb': {'post': str(thumb.pk), 'ig': str(thumb_ig.pk)},
    'video': {'post': str(video.pk), 'ig': str(video_ig.pk)},
}))
