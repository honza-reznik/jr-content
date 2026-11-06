"""RBAC middleware that resolves org role + workspace role on every request."""

from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.http import Http404

from .models import OrgMembership, WorkspaceMembership

# A write on a URL without a workspace or organization in it must name its
# target organization itself: the session's active organization is shared by
# every tab and can change between rendering a form and submitting it.
# Plain forms send ORG_TARGET_FIELD; htmx requests get ORG_TARGET_HEADER from
# the page that issued them (base.html).
ORG_TARGET_FIELD = "organization_id"
ORG_TARGET_HEADER = "X-JRC-Organization-Id"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")


def _is_background_request(request):
    return bool(request.headers.get("HX-Request")) or request.headers.get("X-Requested-With") == "XMLHttpRequest"


def activate_organization(request, organization):
    """Make ``organization`` the active one after a write to it succeeded.

    Writes on global URLs are bound to the organization their page showed,
    which may differ from the session's selection (another tab). Views call
    this only once the change was actually made, so the page they redirect to
    shows the organization that was changed; refused or invalid submissions
    never move the selection.
    """
    request.session["active_organization_id"] = str(organization.pk)


# Prefixes served straight off disk by a URLconf route rather than by a view.
# Nothing downstream of them reads request.org / request.workspace, so
# resolving that context would only cost a session read, a user query and two
# membership queries — once per file, and a media-heavy page requests dozens.
CONTEXT_FREE_PATH_PREFIXES = ("/static/", "/media/")


class RBACMiddleware:
    """Attach org and workspace context to the request.

    Sets:
        request.org - the user's Organization (or None)
        request.org_membership - the user's OrgMembership (or None)
        request.workspace - the current Workspace (or None, set by views)
        request.workspace_membership - the WorkspaceMembership (or None)
        request.org_target_bound - True when request.org was named by the
            request itself (workspace in the URL, or the organization the
            submitted page belongs to), not taken from the shared session

    Global pages follow the selected organization, or the last accessible
    workspace's organization. Workspace URLs always override that selection.
    Writes on global URLs are bound to the organization the submitting page
    showed (ORG_TARGET_FIELD / ORG_TARGET_HEADER), never to the session.
    Organization roles are resolved independently of workspace roles.

    Resolution happens in process_view (not __call__) because
    request.resolver_match is only available after URL resolution,
    which occurs inside get_response.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Initialize attributes; actual resolution happens in process_view
        request.org = None
        request.org_membership = None
        request.workspace = None
        request.workspace_membership = None
        request.organization_memberships = []
        request.org_target_bound = False
        request.navigated_workspace_membership = None

        if request.path_info.startswith(CONTEXT_FREE_PATH_PREFIXES):
            return self.get_response(request)

        # Resolve org and workspace context early.
        # Workspace-from-URL resolution happens in process_view() (after URL
        # resolution). Here we only resolve org directly and fall back to
        # last_workspace_id for global pages that have no workspace_id in the URL.
        if hasattr(request, "user") and request.user.is_authenticated:
            memberships = list(
                OrgMembership.objects.filter(user=request.user, organization__deleted_at__isnull=True)
                .select_related("organization")
                .order_by("organization__created_at", "organization_id")
            )
            request.organization_memberships = memberships
            last_membership = (
                WorkspaceMembership.objects.filter(
                    user=request.user,
                    workspace_id=request.user.last_workspace_id,
                    workspace__is_archived=False,
                    workspace__organization__deleted_at__isnull=True,
                )
                .select_related("workspace__organization", "custom_role")
                .first()
                if request.user.last_workspace_id
                else None
            )
            selected_id = request.session.get("active_organization_id")
            selected = next((m for m in memberships if str(m.organization_id) == selected_id), None)
            if selected is None and last_membership:
                selected = next(
                    (m for m in memberships if m.organization_id == last_membership.workspace.organization_id), None
                )
            if selected is None and last_membership:
                # Legacy workspace-only members keep their workspace context,
                # but receive no org administration rights. The repair command
                # can explicitly backfill their MEMBER membership.
                request.org = last_membership.workspace.organization
                request.workspace = last_membership.workspace
                request.workspace_membership = last_membership
            elif selected is None:
                selected = memberships[0] if memberships else None
            if selected:
                request.org = selected.organization
                request.org_membership = selected
                if last_membership and last_membership.workspace.organization_id == selected.organization_id:
                    request.workspace = last_membership.workspace
                    request.workspace_membership = last_membership

        response = self.get_response(request)
        navigated = request.navigated_workspace_membership
        if navigated is not None and 200 <= response.status_code < 300:
            # The workspace page was actually shown: it becomes the current
            # workspace and its organization the active one. A refused or
            # failed request (403/404, error redirect) changes neither.
            if request.user.last_workspace_id != navigated.workspace_id:
                request.user.last_workspace_id = navigated.workspace_id
                request.user.save(update_fields=["last_workspace_id"])
            request.session["active_organization_id"] = str(navigated.workspace.organization_id)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Resolve workspace context from URL kwargs.

        This runs after URL resolution, so resolver_match and view_kwargs
        are available.
        """
        # workspace_id first: it is a dict lookup, while ``request.user`` is
        # lazy and forces a session read plus a user query on every URL that
        # has no workspace in it at all.
        workspace_id = view_kwargs.get("workspace_id")
        if not workspace_id:
            if request.method not in SAFE_METHODS and "org_id" not in view_kwargs:
                self._bind_org_target(request, view_func)
            return None

        if not hasattr(request, "user") or not request.user.is_authenticated:
            return None

        # Clear stale context from __call__ before resolving
        request.workspace = None
        request.workspace_membership = None

        ws_membership = (
            WorkspaceMembership.objects.filter(
                user=request.user,
                workspace_id=workspace_id,
                workspace__organization__deleted_at__isnull=True,
            )
            .select_related("workspace__organization", "custom_role")
            .first()
        )
        hide_unavailable_workspace = request.resolver_match.namespace in (
            "jrc_production",
            "jrc_texts",
            "jrc_thumbnails",
        ) or (request.resolver_match.namespace == "jrc_video" and request.resolver_match.url_name == "final_download")
        if not ws_membership:
            if hide_unavailable_workspace:
                raise Http404
            raise PermissionDenied("You do not have access to this workspace.")

        if ws_membership.workspace.is_archived and request.resolver_match.namespace in (
            "jrc_production",
            "jrc_texts",
            "jrc_thumbnails",
        ):
            raise Http404

        request.workspace = ws_membership.workspace
        request.workspace_membership = ws_membership

        # Opening a workspace page (a full-page GET, not a background htmx/XHR
        # request or a write) makes it current once the response shows it was
        # allowed; see __call__. Nothing changes before the view's role checks.
        if request.method in SAFE_METHODS and not _is_background_request(request):
            request.navigated_workspace_membership = ws_membership

        # Also resolve org from workspace for consistency
        org = ws_membership.workspace.organization
        org_membership = (
            OrgMembership.objects.filter(
                user=request.user,
                organization=org,
            )
            .select_related("organization")
            .first()
        )
        request.org = org
        request.org_membership = org_membership
        request.org_target_bound = True

        return None

    @staticmethod
    def _bind_org_target(request, view_func):
        """Bind a global-URL write to the organization named by the request.

        Without a named target the session context stays for display only
        (``org_target_bound`` False) and org-scoped writes refuse it. A named
        organization the user is not an active member of leaves no context.
        """
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return
        # CSRF-exempt endpoints (bearer-token API, webhooks) are not page forms;
        # reading their body here could consume a stream the view still needs.
        target_id = None
        if not getattr(view_func, "csrf_exempt", False):
            target_id = request.POST.get(ORG_TARGET_FIELD)
        if not target_id:
            target_id = request.headers.get(ORG_TARGET_HEADER)
        if not target_id:
            return
        try:
            target_id = UUID(str(target_id))
        except (TypeError, ValueError, AttributeError):
            target_id = None
        membership = next(
            (m for m in request.organization_memberships if m.organization_id == target_id),
            None,
        )
        if membership is None:
            request.org = None
            request.org_membership = None
            request.workspace = None
            request.workspace_membership = None
            return
        if request.workspace is not None and request.workspace.organization_id != membership.organization_id:
            request.workspace = None
            request.workspace_membership = None
        request.org = membership.organization
        request.org_membership = membership
        request.org_target_bound = True
