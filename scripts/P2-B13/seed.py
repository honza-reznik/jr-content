"""Vymyšlená data pro živý test P2-B13 (workspace = jeden klient, admin přehled); jen projekt jrc-p2b13.

Spouštět: docker exec -i -e JRC_PROJECT=jrc-p2b13 jrc-p2b13-jrc-bb-web-1 python manage.py shell < seed.py
Výstup (s přihlašovacími cookies) patří jen do ignorovaného ops/test-output/P2-B13/fixture.json.
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
from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content, set_publication_date
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p2b13", "Pouze vlastní ověřovací projekt jrc-p2b13!"
run = secrets.token_hex(2)
organization = Organization.objects.create(name=f"P2-B13 Agency {run}")
ella = Workspace.objects.create(organization=organization, name="Ella Studio", approval_workflow_mode="optional")
bruno = Workspace.objects.create(organization=organization, name="Bruno Bakery", approval_workflow_mode="optional")
secret = Workspace.objects.create(
    organization=organization,
    name="Secret Client with a rather long name for overflow checks",
    approval_workflow_mode="optional",
)
people = {}


def person(key, name, org_role, memberships):
    user = User.objects.create(
        email=f"{key}-{secrets.token_hex(3)}@p2-b13.example.invalid", name=name, tos_accepted_at=timezone.now()
    )
    user.set_unusable_password()
    OrgMembership.objects.create(organization=organization, user=user, org_role=org_role)
    for workspace, role in memberships:
        WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    user.last_workspace_id = memberships[0][0].id if memberships else None
    user.save()
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session["active_organization_id"] = str(organization.pk)
    session.save()
    people[key] = {"email": user.email, "cookie": {"name": settings.SESSION_COOKIE_NAME, "value": session.session_key}}
    return user


# Owner of the organization: member of Ella only. Admin: manager in Bruno only. Editor: Ella and Bruno. Client: Ella.
owner = person("owner", "Olga Owner", "owner", [(ella, "owner")])
admin = person("admin", "Adam Admin", "admin", [(bruno, "manager")])
editor = person("editor", "Eda Editor", "member", [(ella, "editor"), (bruno, "editor")])
client = person("client", "Ella Client", "member", [(ella, "client")])
author = User.objects.create(email=f"author-{run}@p2-b13.example.invalid", name="Agency Author")
for workspace in (ella, bruno, secret):
    WorkspaceMembership.objects.create(workspace=workspace, user=author, workspace_role="owner")

channels = {
    "ella": create_manual_channel(ella, "instagram", "Ella Instagram"),
    "bruno": create_manual_channel(bruno, "tiktok", "Bruno TikTok"),
    "secret": create_manual_channel(secret, "youtube", "Secret YouTube"),
}
posts = {}
tomorrow = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
for key, workspace in (("ella", ella), ("bruno", bruno), ("secret", secret)):
    label = workspace.name.split()[0]
    idea = create_content(workspace, f"{label} morning idea", author)
    scripted = create_content(workspace, f"{label} behind the scenes", author, social_account_ids=[channels[key].pk])
    move_content(scripted, "script", author, expected_stage="idea")
    planned = create_content(workspace, f"{label} launch reel", author, social_account_ids=[channels[key].pk])
    set_publication_date(planned, PlatformPost.objects.get(post=planned).pk, author, tomorrow)
    PostComment.objects.create(post=planned, author=author, body=f"Team note for {label}", visibility="internal")
    PostComment.objects.create(post=planned, author=author, body=f"Note for {label}", visibility="external")
    posts[key] = {"idea": str(idea.pk), "scripted": str(scripted.pk), "planned": str(planned.pk)}
long_title = create_content(
    secret,
    "A very long content title that keeps going to check that cards wrap their text instead of overflowing the column",
    author,
)
posts["secret"]["long"] = str(long_title.pk)

print(
    "FIXTURE "
    + json.dumps(
        {
            "people": people,
            "posts": posts,
            "workspaces": {"ella": str(ella.pk), "bruno": str(bruno.pk), "secret": str(secret.pk)},
            "organization": str(organization.pk),
            "date": tomorrow.date().isoformat(),
        }
    )
)
