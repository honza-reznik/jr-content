"""Server-side inventory of browser writes (P1-B25).

Runs after RBAC resolves the URL workspace. Unknown app endpoints fail closed
for unsafe methods. Domain services still enforce workflow, draft ownership,
and per-object constraints; this gate supplies the missing common role check.
"""

from dataclasses import dataclass
from uuid import UUID

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404

from apps.members.permissions import require_authored_content


@dataclass(frozen=True)
class WriteRule:
    permissions: tuple[str, ...] = ()
    owned_object: str = ""
    domain: str = ""
    # Comment writers (add/reply/edit/delete/resolve) of every surface share ``can_comment``.
    comment: bool = False


WRITE_RULES = {}


def _rules(namespace, names, *permissions, owned_object="", domain="", comment=False):
    for name in names.split():
        WRITE_RULES[f"{namespace}:{name}"] = WriteRule(permissions, owned_object, domain, comment)


_rules(
    "composer",
    "idea_create idea_group_create idea_group_delete idea_group_reorder "
    "save_post autosave preview category_create category_edit category_delete template_delete "
    "csv_upload csv_preview csv_confirm_import tag_create feed_add feed_delete attach_pending_media",
    "create_posts",
)
_rules("composer", "idea_edit idea_delete idea_move idea_create_post", "create_posts", owned_object="idea")
_rules(
    "composer",
    "save_post_edit autosave_edit post_delete attach_media remove_media",
    "create_posts",
    owned_object="post",
)
_rules("composer", "save_as_template clone_post", "create_posts")
_rules("composer", "thumbnail_upload upload_media idea_upload_media", "create_posts", "upload_media")
_rules("composer", "upload_media_post unsplash_import_post", "create_posts", "upload_media", owned_object="post")
_rules("composer", "unsplash_import", "create_posts", "upload_media")
_rules("composer", "remove_pending_media", "create_posts", owned_object="pending_upload")
_rules("composer", "transition_platform_post", "create_posts", domain="transition")
_rules("calendar", "reschedule bulk_platform_action", "create_posts")
_rules("calendar", "queue_create queue_delete queue_reorder queue_entry_remove queue_entry_reslot", "publish_directly")
_rules(
    "calendar",
    "save_posting_slot delete_posting_slot toggle_posting_slot_day update_posting_slot",
    "manage_social_accounts",
)
_rules("calendar", "event_create event_edit event_delete", "create_posts")
_rules("approvals", "approve request_changes reject bulk_action", "approve_posts")
_rules("approvals", "resume", "approve_posts", domain="team_review")
_rules("approvals", "add_comment edit_comment delete_comment", domain="member", comment=True)
_rules("media_library", "upload asset_star", "upload_media")
_rules("media_library", "asset_edit asset_tags asset_move version_restore", "edit_media")
_rules("media_library", "asset_delete", "delete_media")
_rules("media_library", "folder_create folder_rename folder_delete", "manage_media")
_rules(
    "inbox",
    "send_reply send_reply_draft add_note assign change_status change_sentiment bulk_action",
    "reply_from_inbox",
)
_rules("inbox", "save_reply_draft discard_reply_draft", "use_inbox")
_rules("inbox", "saved_reply_create saved_reply_edit saved_reply_delete sla_config", "manage_workspace_settings")
_rules(
    "social_accounts",
    "connect connect_bluesky connect_mastodon connect_devto reconnect retry_webhooks disconnect",
    "manage_social_accounts",
)
_rules("onboarding", "create_link revoke_link send_link_email", "manage_social_accounts")
_rules("onboarding", "dismiss_checklist", domain="member")
_rules(
    "onboarding",
    "connection_bluesky connection_mastodon connection_oauth_start connection_done",
    domain="connection_token",
)
# JR services intentionally allow clients to create ideas, propose dates, and
# comment. They check action, author, draft owner, workflow stage and role.
_rules(
    "jrc_production",
    "create quick_idea move content_detail shotlist_action mark_published approval_decision",
    domain="service",
)
_rules(
    "jrc_production",
    "add_content_comment reply_to_content_comment resolve_content_comment",
    domain="service",
    comment=True,
)
_rules("jrc_texts", "script concept concept_to_script", domain="service")
_rules("jrc_video", "upload_start upload_part upload_complete upload_abort video_final", domain="service")
_rules("jrc_thumbnails", "upload", domain="service")

# Exact exceptions, never a namespace wildcard. These resolve their own user,
# organization, portal token or signed service identity; no URL workspace exists.
# P1-B23 owns the organization/workspace settings checks and their tests.
DELEGATED_WRITES = {
    "accounts:account_login": "allauth session authentication",
    "accounts:account_signup": "allauth invitation token",
    "accounts:socialaccount_signup": "allauth invitation token",
    "accounts:accept_terms": "authenticated current user",
    "accounts:settings": "authenticated current user (including password recheck)",
    "accounts:logout": "CSRF protected logout",
    "accounts:dismiss_review_banner": "authenticated current user",
    "organizations:settings": "organization administrator; P1-B23",
    "organizations:switch": "authenticated organization member; P1-B23",
    "members:invite": "organization administrator; P1-B23",
    "members:resend_invite": "organization administrator; P1-B23",
    "members:revoke_invite": "organization administrator; P1-B23",
    "members:accept_invite": "invitation token and matching email; P1-B24",
    "members:update_role": "organization administrator; P1-B23",
    "members:remove": "organization administrator; P1-B23",
    "members:manage_workspaces": "organization administrator; P1-B23",
    "workspaces:create": "organization administrator; P1-B23",
    "workspaces:settings": "workspace owner/manager; P1-B23",
    "workspaces:approvals_settings": "workspace owner/manager; P1-B23",
    "settings_manager:index": "staff only",
    "api_keys:issue": "organization manage_api_keys and grant intersection; P1-B23",
    "api_keys:revoke": "organization manage_api_keys; P1-B23",
    "api_keys:edit": "organization manage_api_keys and grant intersection; P1-B23",
    "media_library_org:shared_upload": "organization owner/admin",
    "media_library_org:shared_asset_edit": "organization owner/admin",
    "media_library_org:shared_asset_delete": "organization owner/admin",
    "media_library_org:shared_asset_tags": "organization owner/admin",
    "notifications:mark_as_read": "notification recipient",
    "notifications:mark_all_read": "authenticated current user",
    "notifications:preferences": "authenticated current user",
    "notifications:unsubscribe": "signed unsubscribe token",
    "social_accounts:select_account": "signed OAuth state and session workspace",
    "client_portal_admin:invite_client": "workspace manager (feature off in MVP)",
    "client_portal_admin:send_magic_link": "workspace manager (feature off in MVP)",
    "client_portal_admin:remove_client": "workspace manager (feature off in MVP)",
    "client_portal:approve": "portal token and allowed workspace posts",
    "client_portal:request_changes": "portal token and allowed workspace posts",
    "client_portal:reject": "portal token and allowed workspace posts",
    "client_portal:request_hold": "portal token and allowed workspace posts",
    "client_portal:magic_link_entry": "one-use portal token confirmed by POST",
    "jrc_video:comment_event": "internal HMAC signature, replay and time checks",
    "jrc_channels:profile": "workspace owner/manager; P1-B23",
    "jrc_channels:color": "workspace owner/manager; P1-B23",
    "jrc_channels:icon": "workspace owner/manager; P1-B23",
    "jrc_channels:edit": "workspace owner/manager; P1-B23",
    "jrc_channels:toggle": "workspace owner/manager; P1-B23",
    "apps.jrc_video.views.video_logout": "CSRF protected logout",
    "video_sso": "authenticated active workspace member; CSRF protected SSO ticket",
    "resend_webhook": "verified webhook signature",
    "inbox_webhooks:webhook_facebook": "verified webhook signature",
    "inbox_webhooks:webhook_instagram_login": "verified webhook signature",
    "inbox_webhooks:webhook_youtube": "verified webhook signature",
    "oauth2_provider:register": "OAuth dynamic registration policy and rate limit",
}


# Reviewed reads with incidental writes (last workspace, read receipts, cached
# analytics), but no form/body mutation. An unsafe method gets 405 (the view only
# reads); an endpoint in none of the three registries gets 403.
READ_VIEWS = {
    "members:list",
    "social_accounts:oauth_callback",
    "composer:idea_board",
    "composer:media_picker",
    "composer:thumbnail_picker",
    "composer:media_stream",
    "composer:media_filmstrip",
    "composer:unsplash_search",
    "composer:pinterest_boards",
    "composer:tiktok_creator_info",
    "composer:media_picker_post",
    "composer:drafts_list",
    "composer:use_template",
    "composer:template_picker",
    "composer:tag_list",
    "composer:feed_list",
    "composer:feed_explore",
    "analytics:status",
    "oauth-authorization-server-metadata",
    "oauth-protected-resource-metadata",
    "oauth-protected-resource-metadata-mcp",
    "approvals:comment_attachment",
    "approvals:version_diff",
    "client_portal_admin:client_list",
    "media_library:search",
    "media_library:tag_autocomplete",
    "media_library:asset_download",
    "media_library:version_list",
    "media_library:processing_status",
    "client_portal:dashboard",
    "client_portal:approval_queue",
    "client_portal:published",
    "client_portal:activity",
    "client_portal:reports",
    "notifications:list",
    "notifications:drawer",
    "notifications:unread_count",
    "onboarding:checklist_partial",
    "onboarding:connection_page",
    "onboarding:oauth_callback",
    "media_library_org:shared_tag_autocomplete",
    "media_library_org:shared_asset_download",
    "media_library_org:shared_version_list",
    "jrc_production:board",
    "jrc_video:final_download",
    "jrc_thumbnails:download",
    "apps.media_library.file_views.private_media",
    "jrc_private_file",
    "health_check",
    "organizations:workspaces",
    "dashboard",
    "organizations:cross_workspace_calendar",
    "calendar:calendar",
    "inbox:message_detail",
    "analytics:post_detail",
    "analytics:account",
    "api_keys:list",
    "api_keys:workspace_options",
    "workspaces:list",
    "social_accounts:list",
    "composer:create_landing",
    "composer:compose",
    "composer:compose_edit",
    "composer:category_list",
    "composer:template_list",
    "calendar:posting_slots",
    "calendar:account_slots_partial",
    "calendar:queue_list",
    "calendar:queue_detail",
    "calendar:publish_tab_queue",
    "calendar:publish_tab_drafts",
    "calendar:publish_tab_approvals",
    "calendar:publish_tab_sent",
    "inbox:feed",
    "inbox:saved_replies",
    "analytics:index",
    "media_library:index",
    "media_library:asset_detail",
    "client_portal:magic_link_expired",
    "media_library_org:shared_index",
    "media_library_org:shared_asset_detail",
    "jrc_ui:about",
    "jrc_ui:license",
    "jrc_ui:source_code",
    "jrc_video:upload_status",
    "jrc_video:upload_resume",
}


def enforce_write_rule(request, rule, kwargs):
    """Require the effective permission, then scope optional author checks."""
    if rule.domain == "connection_token":
        from apps.onboarding.models import ConnectionLink

        link = get_object_or_404(ConnectionLink.objects.select_related("workspace"), token=kwargs["token"])
        if not link.is_active or link.workspace.is_archived:
            raise PermissionDenied("This connection link is no longer active.")
        return
    if rule.domain == "service" and "workspace_id" not in kwargs:
        # Board writers resolve the submitted workspace, never last_workspace. A workspace
        # the user does not belong to is reported as missing, like a foreign URL workspace.
        from apps.members.models import WorkspaceMembership

        try:
            workspace_id = UUID(request.POST.get("workspace", ""))
        except (ValueError, TypeError, AttributeError):
            raise Http404 from None
        membership = (
            WorkspaceMembership.objects.select_related("workspace", "user", "custom_role")
            .filter(user=request.user, workspace_id=workspace_id)
            .first()
        )
        if membership is None:
            raise Http404
    else:
        membership = getattr(request, "workspace_membership", None)
    if not request.user.is_active or not membership:
        raise PermissionDenied("Workspace membership required.")
    if str(membership.workspace_id) != str(kwargs.get("workspace_id", membership.workspace_id)):
        raise Http404
    if membership.workspace.is_archived:
        # Same answer as RBAC gives members elsewhere; JR pages already hid it with a 404.
        raise PermissionDenied("This workspace is archived.")
    if rule.comment:
        from apps.members.permissions import can_comment

        if not can_comment(membership):
            raise PermissionDenied("You cannot comment in this workspace.")
    permissions = membership.effective_permissions
    if any(not permissions.get(key, False) for key in rule.permissions):
        raise PermissionDenied("You cannot perform this action in this workspace.")
    # Idea create/edit and form saves can upload files as well as write text.
    # A custom role's text permission must never imply upload_media.
    if request.resolver_match.namespace == "composer" and request.FILES and not permissions.get("upload_media", False):
        raise PermissionDenied("Media upload permission required.")
    if rule.domain in {"team_review", "transition"} and membership.workspace_role in {"client", "viewer"}:
        raise PermissionDenied("Only team members may use this action.")
    if rule.domain == "transition":
        target = request.POST.get("target_status", "").strip()
        if target in {"approved", "pending_client", "changes_requested", "rejected"}:
            if not permissions.get("approve_posts", False):
                raise PermissionDenied("Approval permission required.")
            # Clients decide through the approval service, which checks the
            # client-review stage. This low-level chip endpoint edits content.
            return
        if not permissions.get("create_posts", False):
            raise PermissionDenied("Content editing permission required.")
        if target in {"scheduled", "publishing", "published"} and not permissions.get("publish_directly", False):
            raise PermissionDenied("Publishing permission required.")
        _require_author(request, membership, "post", kwargs)
    if rule.owned_object:
        _require_author(request, membership, rule.owned_object, kwargs)


def _require_author(request, membership, kind, kwargs):
    from apps.composer.models import Idea, Post
    from apps.media_library.models import MediaAsset

    if kind == "pending_upload":
        asset = get_object_or_404(MediaAsset, pk=kwargs["asset_id"], workspace_id=membership.workspace_id)
        pending = request.session.get(f"pending_media_{membership.workspace_id}", [])
        if asset.uploaded_by_id != request.user.pk or str(asset.pk) not in pending:
            raise PermissionDenied("Only your pending uploads may be removed here.")
        return
    model = Post if kind == "post" else Idea
    obj = get_object_or_404(model, pk=kwargs[f"{kind}_id"], workspace_id=membership.workspace_id)
    require_authored_content(membership.effective_permissions, request.user.pk, obj.author_id)


class WriteAccessMiddleware:
    """Cover normal, HTMX and unsafe non-POST requests through one gate."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        match = request.resolver_match
        key = match.view_name
        if key in WRITE_RULES:
            # All browser writers currently accept POST. PUT/PATCH/DELETE must
            # not sneak through a handler that branches only on GET vs other.
            if request.method != "POST":
                return HttpResponseNotAllowed(["POST"])
            rule = WRITE_RULES[key]
            if not request.user.is_authenticated and rule.domain != "connection_token":
                # Same answer as the views' own ``login_required``; nothing below runs.
                return redirect_to_login(request.get_full_path())
            enforce_write_rule(request, rule, view_kwargs)
        elif key in DELEGATED_WRITES:
            return None
        elif key in READ_VIEWS:
            return HttpResponseNotAllowed(["GET", "HEAD"])
        elif getattr(view_func, "__module__", "").startswith("apps."):
            # Browser reads and new endpoints have no implicit write rights.
            raise PermissionDenied("This endpoint has no registered write permission.")
        return None


# These features are disabled in MVP; their own org-scoped decorators remain
# authoritative when the upstream regression suite enables them.
for _name in [
    "playground",
    "subscribe",
    "checkout",
    "discard-checkout",
    "recover",
    "portal",
    "billing-settings",
    "update-billing-contact",
    "score-packaging",
    "score-video-hook",
    "benchmark-channel",
    "benchmark-video",
    "research-content-gaps",
    "list-niches",
]:
    DELEGATED_WRITES[f"intelligence:{_name}"] = "require_org_permission (billing or use_intelligence)"
DELEGATED_WRITES["intelligence_global:activate"] = "authenticated owner of pending subscription"
