import os
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

email = "f8-b01-visual@example.invalid"
password = os.environ["F8_VISUAL_PASSWORD"]
user, _ = User.objects.get_or_create(email=email, defaults={"tos_accepted_at": timezone.now()})
user.tos_accepted_at = timezone.now()
user.set_password(password)
user.save()
organization, _ = Organization.objects.get_or_create(
    name="F8-B01 Visual Test", defaults={"default_timezone": "Europe/Prague"}
)
OrgMembership.objects.get_or_create(
    user=user, organization=organization, defaults={"org_role": OrgMembership.OrgRole.OWNER}
)
workspace, _ = Workspace.objects.get_or_create(
    organization=organization, name="F8-B01 Calendar Demo"
)
WorkspaceMembership.objects.get_or_create(
    user=user, workspace=workspace, defaults={"workspace_role": WorkspaceMembership.WorkspaceRole.OWNER}
)
for platform, label in (("instagram", "Instagram"), ("linkedin_company", "LinkedIn")):
    if not workspace.social_accounts.filter(account_name=label).exists():
        create_manual_channel(workspace, platform, label, display_name=label)

fixtures = (
    ("Visual: launch story", PlatformPost.Status.PENDING_REVIEW, 2),
    ("Visual: studio notes", PlatformPost.Status.SCHEDULED, 4),
)
for title, status, offset in fixtures:
    post = workspace.posts.filter(title=title).first()
    if post is None:
        post = create_content(workspace, title, user)
    when = timezone.now() + timedelta(days=offset)
    for child in post.platform_posts.all():
        child.status = status
        child.scheduled_at = when
        child.save(update_fields=["status", "scheduled_at", "updated_at"])
    if title.endswith("launch story") and not post.comments.exists():
        PostComment.objects.create(post=post, author=user, body="INTERNAL F8 TEST COMMENT MUST STAY PRIVATE")

print(workspace.pk)
