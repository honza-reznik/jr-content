"""Shared workspace permission checks."""


def can_publish_directly(membership):
    """Whether this workspace member may publish content and download final videos."""
    return bool(
        membership
        and membership.workspace_role not in {"client", "viewer"}
        and membership.effective_permissions.get("publish_directly", False)
    )
