"""Run only in jrc-f2b08; temporary session stays in the project's media volume."""
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.jrc_channels.management.commands.jrc_seed_demo import DEMO_WORKSPACE_ID

user, _ = get_user_model().objects.get_or_create(
    email="profile-check@example.invalid",
    defaults={"name": "Local Editor", "tos_accepted_at": timezone.now()},
)
call_command("jrc_seed_demo", owner_email=user.email)
client = Client()
client.force_login(user)
Path("/app/media/f2b08-session.json").write_text(
    json.dumps({"workspace": str(DEMO_WORKSPACE_ID), "session": client.cookies["sessionid"].value})
)
print("Místní demo a dočasná relace připraveny.")
