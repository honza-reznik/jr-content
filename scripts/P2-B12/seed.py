"""Only synthetic data in jrc-p2b12; no external requests."""
import json
import os
import secrets
from pathlib import Path

from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.icons import validated_favicon
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace
from PIL import Image
from io import BytesIO

assert os.environ["JRC_PROJECT"] == "jrc-p2b12"
assert not os.environ.get("JRC_SMTP_HOST")
organization = Organization.objects.create(name="P2-B12 test")
workspace = Workspace.objects.create(organization=organization, name="Synthetic client P2-B12", approval_workflow_mode="optional")
password = secrets.token_urlsafe(24)
user = User.objects.create_user(email="p2b12-owner@example.invalid", password=password, name="Test owner", tos_accepted_at=timezone.now())
OrgMembership.objects.create(user=user, organization=organization, org_role="owner")
WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
data = {"email": user.email, "password": password, "workspace": str(workspace.pk), "channels": {}}
for platform, name in [("instagram", "Instagram"), ("tiktok", "TikTok"), ("youtube", "YouTube"), ("facebook", "Facebook"), ("custom", "Forendors"), ("custom", "Long channel name for a custom membership community and extra words to check wrapping")]:
    account = create_manual_channel(workspace, platform, name, display_name=name, profile_url="https://example.invalid/profile")
    if platform == "custom":
        body = BytesIO()
        image = Image.new("RGB", (32,32), "#047857")
        for x in range(8,24):
            for y in range(8,24):
                image.putpixel((x,y), (255,255,255))
        image.save(body, format="PNG")
        account.jrc_channel.favicon = validated_favicon(body.getvalue())
        account.jrc_channel.save()
    post = create_content(workspace, name + " colour test", user, social_account_ids=[account.pk])
    variant = post.platform_posts.get()
    variant.status = "scheduled"
    variant.scheduled_at = timezone.now()
    variant.save()
    data["channels"][name] = {"id":str(account.pk), "detail": f"/jrc/workspace/{workspace.pk}/content/{post.pk}/"}
data["profile"] = f"/jrc/workspace/{workspace.pk}/client-profile/"
from django.urls import reverse
data["profile"] = reverse("jrc_channels:profile",kwargs={"workspace_id":workspace.pk})
output = Path("output/p2b12-browser.env.local")
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(data))
output.chmod(0o600)
print("Synthetic P2-B12 data ready.")
