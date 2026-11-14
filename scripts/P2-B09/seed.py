"""Only fictitious data in jrc-p2b09; sessions remain in ignored test output."""

import json
import os
from pathlib import Path

from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import PostComment
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

assert os.environ["JRC_PROJECT"] == "jrc-p2b09"
with override_settings(JRC_FF_SYNC_ENABLED=False):
    org, _ = Organization.objects.get_or_create(name="P2-B09 laboratory")
    workspace, _ = Workspace.objects.get_or_create(organization=org, name="Fictional Board Studio", defaults={"approval_workflow_mode": "required_internal_and_client"})
    from apps.composer.models import Post

    Post.all_objects.filter(workspace=workspace).delete()
    account = workspace.social_accounts.first() or create_manual_channel(workspace, "instagram", "Fictional Instagram")
    users, sessions = {}, {}
    for role in ["owner", "manager", "editor", "contributor", "client", "viewer"]:
        actor, _ = User.objects.get_or_create(email=f"p2b09-{role}@example.invalid", defaults={"name": f"Board {role}", "tos_accepted_at": timezone.now()})
        OrgMembership.objects.update_or_create(user=actor, organization=org, defaults={"org_role": "owner" if role == "owner" else "member"})
        WorkspaceMembership.objects.update_or_create(user=actor, workspace=workspace, defaults={"workspace_role": role})
        users[role] = actor
        http = Client()
        http.force_login(actor)
        sessions[role] = http.session.session_key
    posts = {}
    titles = ["Drag idea", "Bulk idea one", "Bulk idea two", "Trash and restore", "Approval request", "Team review", "Client review one", "Client review two", "Search target", "Long title: " + "Fictional production notes with a very long name " * 5]
    for index, title in enumerate(titles):
        post = create_content(workspace, title[:255], users["editor"] if title == "Approval request" else users["owner"], social_account_ids=[account.pk], description="A searchable concept: sapphire lighthouse" if title == "Search target" else "")
        posts[title if index < 9 else "Long title"] = str(post.pk)
        if title in {"Team review", "Client review one", "Client review two"}:
            post = move_content(post, "review", users["owner"], expected_stage="idea")
            if title.startswith("Client review"):
                move_content(post, "approved", users["owner"], expected_stage="review")
    search = workspace.posts.get(pk=posts["Search target"])
    PostComment.objects.create(post=search, author=users["owner"], body="Public discussion: amber telescope", visibility="external")
    PostComment.objects.create(post=search, author=users["owner"], body="Private discussion: obsidian lantern", visibility="internal")
    private, _ = Workspace.objects.get_or_create(organization=org, name="Private studio")
    Post.all_objects.filter(workspace=private).delete()
    Post.objects.create(workspace=private, title="Forbidden hidden title")
    payload = {"workspace": str(workspace.pk), "posts": posts, "sessions": sessions}
    if os.environ.get("BOARD_VIDEO_SEARCH") == "1":
        from apps.jrc_video.client import FreeFrameUserClient
        from apps.jrc_video.models import VideoAsset, VideoProject
        from apps.jrc_video.services import sync_workspace

        sync_workspace(workspace.pk)
        api = FreeFrameUserClient(users["owner"])
        project = str(VideoProject.objects.get(workspace=workspace).ff_project_id)
        assets = api.request("GET", f"/projects/{project}/assets?include_failed=true")
        reusable = next((asset for asset in assets if asset["name"] == "P2-B09 fictional comment search" and asset["latest_version"]), None)
        # An initialized local upload is enough for comment API tests; no video is downloaded.
        upload = {"asset_id": reusable["id"], "version_id": reusable["latest_version"]["id"]} if reusable else api.request("POST", "/upload/initiate", {
            "project_id": project,
            "asset_name": "P2-B09 fictional comment search",
            "original_filename": "fictional.mp4", "mime_type": "video/mp4", "file_size_bytes": 128,
        })
        VideoAsset.objects.create(platform_post=search.platform_posts.get(), ff_asset_id=upload["asset_id"])
        existing = api.request("GET", f"/assets/{upload['asset_id']}/comments")
        for visibility, body in [("public", "Video public coral meadow"), ("internal", "Video private onyx orchard")]:
            if any(comment["body"] == body for comment in existing):
                continue
            parent = api.request("POST", f"/assets/{upload['asset_id']}/comments", {
                "version_id": upload["version_id"], "body": body, "visibility": visibility,
            })
            api.request("POST", f"/assets/{upload['asset_id']}/comments", {
                "version_id": upload["version_id"], "body": body + " reply", "visibility": visibility,
                "parent_id": parent["id"],
            })
        payload["video_comments"] = True
    output = Path('/app/output/P2-B09/seed.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload))
    output.chmod(0o600)
print("P2-B09: fictitious board data and six local sessions prepared.")
