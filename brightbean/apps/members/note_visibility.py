"""Privacy of history notes is captured when the action is performed."""

from .models import WorkspaceMembership


def note_audience(workspace, user):
    membership = WorkspaceMembership.objects.filter(workspace=workspace, user=user).first() if user else None
    if membership is None:
        return {"actor_role": "", "note_visibility": "internal"}
    return {
        "actor_role": "custom" if membership.custom_role_id else membership.workspace_role,
        "note_visibility": "external" if membership.workspace_role in ("client", "viewer") else "internal",
    }
