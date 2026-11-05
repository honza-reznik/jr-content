"""Vymyšlená data pouze pro jrc-p1b17; výstup patří do ignorovaného souboru."""
import json
import os
import uuid
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_thumbnails import services as thumbnails
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.members.services import create_invitation
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get('JRC_PUBLIC_ORIGIN') == 'http://127.0.0.1:13593'
organization = Organization.objects.create(name='P1-B17 local studio')
workspace = Workspace.objects.create(organization=organization, name='P1-B17 Garden')
sessions, people = {}, {}
suffix = uuid.uuid4().hex[:8]
for role in ['owner', 'client']:
    person = User.objects.create(email=f'{role}-p1b17-{suffix}@example.invalid', name=role.title(), tos_accepted_at=timezone.now())
    OrgMembership.objects.create(organization=organization, user=person, org_role='owner' if role == 'owner' else 'member')
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
    person.last_workspace_id = workspace.pk
    person.save(update_fields=['last_workspace_id'])
    http = Client()
    http.force_login(person)
    sessions[role], people[role] = http.cookies['sessionid'].value, person
account = create_manual_channel(workspace, 'custom', 'Garden channel')
post = create_content(workspace, 'Autumn garden', people['owner'], social_account_ids=[account.pk])
variant = post.platform_posts.get()
variant.platform_specific_caption = 'Three plants for autumn.'
variant.status = PlatformPost.Status.APPROVED
variant.save(update_fields=['platform_specific_caption', 'status'])
image = BytesIO()
Image.new('RGB', (320, 180), '#6D28D9').save(image, 'PNG')
thumbnail = thumbnails.upload_thumbnail(post, variant, people['owner'], SimpleUploadedFile('garden.png', image.getvalue(), content_type='image/png'), thumbnails.member(workspace, people['owner']))
invitation = create_invitation(organization, 'invite-p1b17@example.invalid', 'member', [{'workspace_id': str(workspace.pk), 'role': 'client'}], people['owner'], inviter=people['owner'])
sync_workspace(workspace.pk)
print('SEED=' + json.dumps({
    'workspace': str(workspace.pk), 'post': str(post.pk), 'variant': str(variant.pk),
    'sessions': sessions, 'image': __import__('base64').b64encode(image.getvalue()).decode(),
    'invite': reverse('members:accept_invite', kwargs={'token': invitation.token}),
    'urls': {
        'detail': reverse('jrc_production:content_detail', kwargs={'workspace_id': workspace.pk, 'post_id': post.pk}),
        'profile': reverse('jrc_channels:profile', kwargs={'workspace_id': workspace.pk}),
        'members': reverse('members:list'),
    }
}))
