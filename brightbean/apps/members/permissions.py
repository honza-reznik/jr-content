"""Shared workspace permission checks."""


def can_publish_directly(membership):
    """Whether this workspace member may publish content and download final videos."""
    return bool(
        membership
        and membership.workspace_role != "viewer"
        and (membership.workspace_role == "client" or membership.effective_permissions.get("publish_directly", False))
    )
