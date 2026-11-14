"""Vymyšlená data pro živý test P2-B08 (záložky, automatické ukládání, kanály jako štítky); jen projekt jrc-p2b08.

Spouštět: docker exec -i -e JRC_PROJECT=jrc-p2b08 jrc-p2b08-jrc-bb-web-1 python manage.py shell < seed.py
Výstup (s přihlašovacími cookies) patří jen do ignorovaného ops/test-output/P2-B08/fixture.json.
"""

import json
import os
import secrets
from importlib import import_module

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist as shotlist_services
from apps.jrc_production.services import create_content, move_content
from apps.jrc_texts import services as text_services
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p2b08", "Pouze vlastní ověřovací projekt jrc-p2b08!"
run = secrets.token_hex(2)
organization = Organization.objects.create(name=f"P2-B08 Studio {run}")
ella = Workspace.objects.create(
    organization=organization, name="Ella Studio", approval_workflow_mode="required_internal_and_client"
)
people = {}


def person(key, name, role, org_role="member"):
    user = User.objects.create(
        email=f"{key}-{secrets.token_hex(3)}@p2-b08.example.invalid", name=name, tos_accepted_at=timezone.now()
    )
    user.set_unusable_password()
    user.save()
    OrgMembership.objects.create(organization=organization, user=user, org_role=org_role)
    WorkspaceMembership.objects.create(workspace=ella, user=user, workspace_role=role)
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    people[key] = {"email": user.email, "cookie": {"name": settings.SESSION_COOKIE_NAME, "value": session.session_key}}
    return user


owner = person("owner", "Olga Owner", "owner", "owner")
editor = person("editor", "Eda Editor", "editor")
client = person("client", "Ella Client", "client")
viewer = person("viewer", "Vera Viewer", "viewer")

insta = create_manual_channel(ella, "instagram", "Ella Instagram")
tiktok = create_manual_channel(ella, "tiktok", "Ella TikTok s opravdu dlouhým názvem kanálu pro kontrolu zalamování")
youtube = create_manual_channel(ella, "youtube", "Ella YouTube")
posts = {}


def content(key, title, channels=(), stage="idea", description=""):
    post = create_content(
        ella, title, owner, social_account_ids=[c.pk for c in channels], description=description
    )
    if stage != "idea":
        move_content(post, stage, owner, expected_stage="idea")
    posts[key] = str(post.pk)
    return post


main = content(
    "main",
    "Pottery reel – autumn glaze",
    channels=[insta],
    stage="script",
    description="Hook: hands in wet clay.\nShow the glaze test in three steps.",
)
for text, note in (("Close-up of hands shaping the clay", "Window light"), ("Glaze test plates", "")):
    shotlist_services.add_item(main, owner, text, note)
text_services.save_caption(main.pk, main.platform_posts.get().pk, owner, "First caption for Instagram #pottery")
content("long", "A very long content title " * 6, channels=[insta, tiktok], stage="script")
content("empty", "Content without channels")
print("FIXTURE=" + json.dumps({"ella": str(ella.pk), "channels": {"insta": str(insta.pk), "tiktok": str(tiktok.pk), "youtube": str(youtube.pk)}, "posts": posts, "people": people}))
