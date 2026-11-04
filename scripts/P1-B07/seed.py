"""Vymyšlená data pro Chromium (~40 karet), pouze vlastní ověřovací projekt P1-B07."""

import json
import os
from datetime import timedelta

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import PostComment
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-p1b07", "Pouze vlastní ověřovací projekt!"
now = timezone.now()
organization = Organization.objects.create(name="P1-B07 local studio")
ella_ws = Workspace.objects.create(organization=organization, name="Ella Test", timezone="Europe/Prague")
other_ws = Workspace.objects.create(organization=organization, name="Second Client", timezone="Europe/Prague")


def person(email, name):
    user = User.objects.create_user(email=email, name=name, tos_accepted_at=now)
    # Only the seeded clients, like the real accounts (drop any provisioned personal workspace).
    WorkspaceMembership.objects.filter(user=user).delete()
    return user


owner = person("owner-p1b07@example.invalid", "Olivia Owner")
OrgMembership.objects.create(user=owner, organization=organization, org_role="owner")
WorkspaceMembership.objects.create(user=owner, workspace=ella_ws, workspace_role="owner")
WorkspaceMembership.objects.create(user=owner, workspace=other_ws, workspace_role="owner")
ella = person("ella-p1b07@example.invalid", "Ella Client")
OrgMembership.objects.create(user=ella, organization=organization, org_role="member")
WorkspaceMembership.objects.create(user=ella, workspace=ella_ws, workspace_role="client")
for user, workspace in ((owner, ella_ws), (ella, ella_ws)):
    user.last_workspace_id = workspace.pk
    user.save(update_fields=["last_workspace_id"])

ig = create_manual_channel(ella_ws, "instagram", "Ella Instagram")
tt = create_manual_channel(ella_ws, "tiktok", "Ella TikTok")
yt = create_manual_channel(ella_ws, "youtube", "Ella YouTube long videos channel")
other_ig = create_manual_channel(other_ws, "instagram", "Second IG")

LONG = (
    "Morning routine in the studio with plants, coffee and a slow pan over the shelves, "
    "followed by a short talk about why Ella started the project and what comes next"
)
plan = [
    # (title, stage, channels, proposed in days, comments)
    *[(f"Idea {i}: quick thought about spring plants", "idea", [], None, 0) for i in range(1, 18)],
    (LONG, "idea", [ig, tt, yt], 4, 2),
    ("Dated idea for next week", "idea", [], 7, 0),
    ("Dated idea for tomorrow", "idea", [ig], 1, 1),
    *[(f"Idea {i} with Instagram", "idea", [ig], None, 0) for i in range(18, 23)],
    ("Script: watering mistakes", "script", [ig, tt], 10, 3),
    ("Script: three cuttings", "script", [], None, 0),
    ("Script: balcony tour", "script", [yt], None, 0),
    ("Shooting: repotting monstera", "shooting", [ig, tt], 5, 0),
    ("Shooting: Q&A outdoors", "shooting", [tt], None, 1),
    ("Editing: soil comparison", "editing", [ig], 3, 0),
    ("Editing: unboxing seeds", "editing", [ig, yt], None, 0),
    ("Review: winter care", "review", [ig], 2, 2),
    ("Review: herb garden", "review", [tt, ig], None, 0),
]
order = ["idea", "script", "shooting", "editing"]
for index, (title, stage, channels, days, comments) in enumerate(plan):
    post = create_content(ella_ws, title, owner, social_account_ids=[c.pk for c in channels])
    if stage in order[1:]:
        move_content(post, stage, owner, expected_stage="idea")
    elif stage == "review":
        move_content(post, "review", owner, expected_stage="idea")
    if days is not None:
        Post.objects.filter(pk=post.pk).update(proposed_publish_at=now + timedelta(days=days))
    for number in range(comments):
        visibility = "external" if number % 2 == 0 else "internal"
        PostComment.objects.create(post=post, author=owner, body=f"Note {number}", visibility=visibility)
    Post.objects.filter(pk=post.pk).update(updated_at=now - timedelta(hours=index))
for i in range(1, 7):
    create_content(other_ws, f"Second client idea {i}", owner, social_account_ids=[other_ig.pk] if i % 2 else [])


def session(user):
    http = Client()
    http.force_login(user)
    return http.cookies["sessionid"].value


print(
    json.dumps(
        {
            "ella_ws": str(ella_ws.pk),
            "other_ws": str(other_ws.pk),
            "owner_session": session(owner),
            "client_session": session(ella),
            "total": Post.objects.filter(workspace__organization=organization).count(),
        }
    )
)
