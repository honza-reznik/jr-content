"""Vymyšlená data pro živý test P2-B03 (nástěnka, nový obsah, kalendář); jen vlastní projekt jrc-p2b03.

Spouštět: docker exec -i -e JRC_PROJECT=jrc-p2b03 jrc-p2b03-jrc-bb-web-1 python manage.py shell < seed.py
Výstup (s přihlašovacími cookies) patří jen do ignorovaného ops/test-output/P2-B03/fixture.json.
"""

import json
import os
import secrets
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p2b03", "Pouze vlastní ověřovací projekt jrc-p2b03!"
assert not Organization.objects.filter(name="P2-B03 Studio").exists(), "Data už existují."

organization = Organization.objects.create(name="P2-B03 Studio")
ella = Workspace.objects.create(
    organization=organization, name="Ella Studio", approval_workflow_mode="required_internal_and_client"
)
bravo = Workspace.objects.create(organization=organization, name="Bravo Bakery", approval_workflow_mode="optional")
people = {}


def person(key, name, roles, org_role="member"):
    user = User.objects.create(
        email=f"{key}-{secrets.token_hex(3)}@p2-b03.example.invalid", name=name, tos_accepted_at=timezone.now()
    )
    user.set_unusable_password()
    user.save()
    OrgMembership.objects.create(organization=organization, user=user, org_role=org_role)
    for workspace, role in roles:
        WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    people[key] = {"email": user.email, "cookie": {"name": settings.SESSION_COOKIE_NAME, "value": session.session_key}}
    return user


owner = person("owner", "Olga Owner", [(ella, "owner"), (bravo, "owner")], "owner")
editor = person("editor", "Eda Editor", [(ella, "editor")])
client = person("client", "Ella Client", [(ella, "client")])
viewer = person("viewer", "Vera Viewer", [(ella, "viewer")])

insta = create_manual_channel(ella, "instagram", "Ella Instagram")
tiktok = create_manual_channel(ella, "tiktok", "Ella TikTok s opravdu dlouhým názvem kanálu pro kontrolu zalamování")
youtube = create_manual_channel(bravo, "youtube", "Bravo YouTube")

now = timezone.now()
posts = {}


def content(key, title, user=owner, workspace=ella, channels=(), proposed=None, stage="idea"):
    post = create_content(workspace, title, user, social_account_ids=[c.pk for c in channels])
    if proposed is not None:
        Post.objects.filter(pk=post.pk).update(proposed_publish_at=proposed)
    if stage != "idea":
        move_content(post, stage, owner, expected_stage="idea")
    posts[key] = str(post.pk)
    return post


# Idea column: two dated (later first created), three undated; the newest must end at the bottom.
content("dated_late", "Dated in ten days", proposed=now + timedelta(days=10))
content("undated_old", "Undated oldest idea")
content("dated_soon", "Dated tomorrow", proposed=now + timedelta(days=1))
content("undated_mid", "Undated middle idea " + "with a very long title " * 4)
content("undated_new", "Undated newest idea")
content("script_one", "Script in progress", channels=[insta], stage="script")
content("bravo_idea", "Bravo croissant idea", workspace=bravo, channels=[youtube])
planned = content("planned", "Planned reel for the calendar", channels=[insta])
planned.platform_posts.update(status="scheduled", scheduled_at=now + timedelta(days=2))
planned.scheduled_at = now + timedelta(days=2)
planned.save(update_fields=["scheduled_at"])

print(
    "FIXTURE="
    + json.dumps(
        {
            "ella": str(ella.pk),
            "bravo": str(bravo.pk),
            "channels": {"insta": str(insta.pk), "tiktok": str(tiktok.pk), "youtube": str(youtube.pk)},
            "posts": posts,
            "people": people,
        }
    )
)
