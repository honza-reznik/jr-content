"""P2-INT3: vymyšlená data pro živé ověření integrace (výstupy, duplikát videa, komentáře k verzím, jeden workspace).

Jen projekt jrc-int3: docker exec -i -e JRC_PROJECT=jrc-int3 jrc-int3-jrc-bb-web-1 python manage.py shell < seed.py
"""

import json
import os
import secrets

from django.utils import timezone

from apps.accounts.models import User
from apps.approvals import comments
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist
from apps.jrc_production.services import add_content_channel, create_content
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.jrc_video.models import VideoProject
from apps.jrc_video.services import sync_workspace
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ.get("JRC_PROJECT") == "jrc-int3"
organization, _ = Organization.objects.get_or_create(name="P2-INT3 test studio")
ella, _ = Workspace.objects.get_or_create(
    organization=organization, name="Ella garden", defaults={"approval_workflow_mode": "optional"}
)
bruno, _ = Workspace.objects.get_or_create(
    organization=organization, name="Bruno bakery", defaults={"approval_workflow_mode": "optional"}
)


def person(key, role, name, workspaces):
    user, _ = User.objects.get_or_create(
        email=f"{key}-int3@example.invalid", defaults={"name": name, "tos_accepted_at": timezone.now()}
    )
    password = secrets.token_urlsafe(24)
    user.set_password(password)
    user.last_workspace_id = ella.id
    user.save()
    OrgMembership.objects.get_or_create(
        organization=organization, user=user, defaults={"org_role": "owner" if role == "owner" else "member"}
    )
    for workspace in workspaces:
        WorkspaceMembership.objects.update_or_create(
            workspace=workspace, user=user, defaults={"workspace_role": role}
        )
    return user, {"email": user.email, "password": password}


owner, owner_login = person("owner", "owner", "Honza Team", [ella, bruno])
client, client_login = person("ella", "client", "Ella", [ella])
_, viewer_login = person("viewer", "viewer", "Viewer Vít", [ella])

youtube = create_manual_channel(ella, "youtube", "Ella na YouTube")
instagram = create_manual_channel(ella, "instagram", "Ella Šamánková – zahrada, bylinky a pomalý život (dlouhý název)")
post = create_content(ella, "Garden tour in November", owner, social_account_ids=[youtube.pk, instagram.pk])
add_content_channel(post, youtube.pk, owner, "short_form")
add_content_channel(post, instagram.pk, owner, "story")
bakery_channel = create_manual_channel(bruno, "instagram", "Bruno IG")
create_content(bruno, "Bruno sourdough reel", owner, social_account_ids=[bakery_channel.pk])


def final_text(kind, text, output=None, number=1):
    document, _ = TextDocument.objects.get_or_create(
        post=post,
        kind=kind,
        platform_post=output,
        defaults={"workspace": ella, "format": "plain" if kind == "caption" else "rich"},
    )
    content = (
        {"text": text}
        if kind == "caption"
        else {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
    )
    return TextVersion.objects.create(
        document=document, number=number, state="final", content=content, text=text,
        created_by=owner, finalized_at=timezone.now(),
    )


script_v1 = final_text("script", "Intro in the greenhouse, then the herb beds.")
script_v2 = final_text("script", "Intro at the gate, greenhouse, herb beds, outro by the pond.", number=2)
comments.create_comment(post, owner, "Team only: is the pond outro too long?", text_version_id=script_v2.pk)
shared = comments.create_comment(
    post, owner, "Please confirm the intro location.", visibility="external", text_version_id=script_v1.pk
)
comments.create_comment(post, client, "The gate works for me.", parent_id=shared.pk)

outputs = {pp.output_format + ":" + pp.social_account.platform: pp for pp in post.platform_posts.all()}
shorts = outputs["short_form:youtube"]
caption = final_text("caption", "60 seconds in a November garden #garden", output=shorts)
comments.create_comment(post, owner, "Add a question to the caption?", visibility="external", text_version_id=caption.pk)
final_text("caption", "A slow walk through the garden in November.", output=outputs["long_form:youtube"])

shotlist.add_item(post, owner, "Wide shot of the gate", output_format="long_form")
shotlist.add_item(post, owner, "Greenhouse dolly", output_format="long_form")
shotlist.add_item(post, owner, "Vertical hook: hands in soil", output_format="short_form")
version = shotlist.snapshot(post, "short_form")
comments.create_comment(post, owner, "Shoot the hook first.", visibility="external", shotlist_version_id=version.pk)

sync_workspace(ella.pk)
print(json.dumps({
    "workspace": str(ella.pk),
    "other_workspace": str(bruno.pk),
    "post": str(post.pk),
    "project": str(VideoProject.objects.get(workspace=ella).ff_project_id),
    "owner": owner_login, "client": client_login, "viewer": viewer_login,
}))
