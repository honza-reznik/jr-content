from allauth.account.signals import user_signed_up
from django.dispatch import receiver
from django.utils import timezone


def provision_organization_and_workspace(user):
    """Create a default Organization, Workspace, and memberships for a new user.

    Skips if the user already belongs to an organization (e.g. invited users).
    Safe to call multiple times - the guard is idempotent.
    """
    from apps.members.models import OrgMembership, WorkspaceMembership
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    # Skip if user was invited to an existing org or already provisioned
    if OrgMembership.objects.filter(user=user).exists():
        return

    org = Organization.objects.create(
        name="My Organization",
        default_timezone="UTC",
    )

    OrgMembership.objects.create(
        user=user,
        organization=org,
        org_role=OrgMembership.OrgRole.OWNER,
    )

    # Create a default workspace so the user can start immediately
    workspace = Workspace.objects.create(
        organization=org,
        name="My Workspace",
        description="Your default workspace. Rename it anytime.",
    )

    WorkspaceMembership.objects.create(
        user=user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )

    # Set as last workspace so dashboard redirects here
    user.last_workspace_id = workspace.id
    user.save(update_fields=["last_workspace_id"])


def _set_org_timezone_from_browser(request, user):
    """Update the user's organization timezone from the browser_timezone cookie."""
    from urllib.parse import unquote
    from zoneinfo import available_timezones

    from apps.members.models import OrgMembership

    browser_tz = request.COOKIES.get("browser_timezone", "")
    # Defensive length cap: IANA timezone names are all under 40 chars.
    # Reject anything suspiciously long before decoding/validating.
    if not browser_tz or len(browser_tz) > 64:
        return
    browser_tz = unquote(browser_tz)
    if len(browser_tz) > 64 or browser_tz not in available_timezones():
        return
    membership = OrgMembership.objects.filter(user=user).select_related("organization").first()
    if membership and membership.organization.default_timezone == "UTC":
        membership.organization.default_timezone = browser_tz
        membership.organization.save(update_fields=["default_timezone"])


def _join_invited_org(token, user):
    """Put a just-signed-up user into the org of the invite in their session.

    In invite mode the account adapter already accepted the single-use invite.
    Open-mode invitations are accepted here. Existing organizations are kept.
    """
    from apps.members.models import Invitation, OrgMembership
    from apps.members.services import accept_invitation

    invitation = Invitation.objects.filter(token=token).first()
    if invitation is None:
        return False
    invited_org_id = invitation.organization_id

    if invitation.accepted_at is None:
        if invitation.is_expired:
            return False
        try:
            # Skip the email-match check: the session token is proof of
            # delivery, and social logins return a provider-controlled email
            # that often differs from the invited address.
            accept_invitation(invitation, user, require_email_match=False)
        except ValueError:
            return False
    elif not OrgMembership.objects.filter(user=user, organization_id=invited_org_id).exists():
        return False  # Accepted by someone else.

    return True


@receiver(user_signed_up)
def create_organization_on_signup(sender, request, user, **kwargs):
    """Provision only a standalone signup, after invitation acceptance is known.

    Creating an account through ORM/admin is deliberately membership-free.
    Invitation acceptance is responsible for the invited org and workspace.
    Never delete unrelated memberships as a side effect of joining a team.
    """
    from django.conf import settings

    pending_token = request.session.pop("pending_invite_token", None)
    joined = pending_token and _join_invited_org(pending_token, user)
    if not joined and not pending_token and settings.SIGNUP_MODE == "open":
        provision_organization_and_workspace(user)
        _set_org_timezone_from_browser(request, user)

    if not kwargs.get("sociallogin"):
        user.tos_accepted_at = timezone.now()
        user.save(update_fields=["tos_accepted_at"])
