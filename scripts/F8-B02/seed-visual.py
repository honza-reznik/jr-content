import os
import uuid
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-f8b02", "Pouze vlastní ověřovací projekt!"

email = "f8-b02-visual@example.invalid"
user, _ = User.objects.get_or_create(email=email, defaults={"tos_accepted_at": timezone.now()})
user.tos_accepted_at = timezone.now()
user.set_password(os.environ["F8_B02_VISUAL_PASSWORD"])
user.save()
organization, _ = Organization.objects.get_or_create(
    name="F8-B02 Visual Test", defaults={"default_timezone": "Europe/Prague"}
)
OrgMembership.objects.get_or_create(
    user=user, organization=organization, defaults={"org_role": OrgMembership.OrgRole.OWNER}
)
workspace, _ = Workspace.objects.get_or_create(
    organization=organization,
    name="F8-B02 Publish Demo",
    defaults={"timezone": "Europe/Prague"},
)
WorkspaceMembership.objects.get_or_create(
    user=user, workspace=workspace, defaults={"workspace_role": WorkspaceMembership.WorkspaceRole.OWNER}
)

channels = []
for platform, name in (("instagram", "Ella Instagram"), ("linkedin_company", "Ella LinkedIn")):
    channel = workspace.social_accounts.filter(account_name=name).first()
    if channel is None:
        channel = create_manual_channel(workspace, platform, name, display_name=name)
    channels.append(channel)

post = workspace.posts.filter(title="Visual: autumn launch").first()
if post is None:
    post = create_content(workspace, "Visual: autumn launch", user)
variants = {item.social_account_id: item for item in post.platform_posts.all()}
for index, channel in enumerate(channels):
    variant = variants[channel.pk]
    variant.platform_specific_caption = (
        "A new season, a new story.\n\nDiscover the details in our latest collection. ✨"
        if index == 0
        else "Meet the people and craft behind our autumn launch."
    )
    variant.status = PlatformPost.Status.SCHEDULED if index == 0 else PlatformPost.Status.APPROVED
    variant.scheduled_at = timezone.now() + timedelta(days=2) if index == 0 else None
    variant.save(update_fields=["platform_specific_caption", "status", "scheduled_at", "updated_at"])
    if index == 0:
        video, _ = VideoAsset.objects.get_or_create(
            platform_post=variant,
            defaults={"ff_asset_id": uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")},
        )
        version, _ = VideoVersion.objects.get_or_create(
            video=video,
            number=1,
            defaults={
                "ff_version_id": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                "status": VideoVersion.Status.READY,
                "original_filename": "autumn-launch.mp4",
                "size_bytes": 1024,
            },
        )
        video.final_version = version
        video.final_by = user
        video.final_at = timezone.now()
        video.save(update_fields=["final_version", "final_by", "final_at"])

print(f"F8_B02_WORKSPACE_ID={workspace.pk}")
print(f"F8_B02_POST_ID={post.pk}")
