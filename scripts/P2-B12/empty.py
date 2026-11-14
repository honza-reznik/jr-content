import json
import os
from pathlib import Path

from django.urls import reverse

from apps.accounts.models import User
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

assert os.environ["JRC_PROJECT"] == "jrc-p2b12"
path = Path("output/p2b12-browser.env.local")
data = json.loads(path.read_text())
workspace = Workspace.objects.get(pk=data["workspace"])
user = User.objects.get(email=data["email"])
empty, _ = Workspace.objects.get_or_create(organization=workspace.organization, name="P2-B12 empty client")
WorkspaceMembership.objects.get_or_create(user=user, workspace=empty, defaults={"workspace_role":"owner"})
data["empty_profile"] = reverse("jrc_channels:profile", kwargs={"workspace_id":empty.pk})
path.write_text(json.dumps(data))
print("Empty P2-B12 workspace ready.")
