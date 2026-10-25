"""Team-only comments stay away from clients and viewers of JR Content (F7-B02).

In FreeFrame a comment is "public" or "internal". For accounts managed by JR Content the
two kinds match Brightbean's: the team (owner, manager, editor, contributor, i.e. project
roles owner and editor) writes both kinds, the client (project role reviewer) and a viewer
only ever see and write public comments. Rule applied on the server in every route that
returns or changes a comment; the web interface hides the switch only as a convenience.
"""

import uuid

from sqlalchemy.orm import Session

from ..models.comment import Comment
from ..models.project import ProjectRole
from ..models.user import User
from .permissions import get_project_member

# A reply thread is never deeper than this (COMMENT_TREE_MAX_DEPTH), and cycles cannot be
# stored, but a bound keeps the walk safe whatever the data looks like.
_MAX_WALK = 50


def hides_internal_comments(db: Session, project_id: uuid.UUID, user: User | None) -> bool:
    """True for a managed account that is a reviewer (client), viewer or no member at all."""
    if user is None or user.jrc_managed is not True:
        return False
    member = get_project_member(db, project_id, user.id)
    return member is None or member.role in (ProjectRole.reviewer, ProjectRole.viewer)


def thread_is_internal(db: Session, comment: Comment) -> bool:
    """The comment or any comment above it is team-only (a reply to one is team-only too)."""
    current = comment
    for _ in range(_MAX_WALK):
        if current.visibility == "internal":
            return True
        if current.parent_id is None:
            return False
        current = db.query(Comment).filter(Comment.id == current.parent_id).first()
        if current is None:
            return False
    return True


def internal_comment_ids(comments: list[Comment]) -> set[uuid.UUID]:
    """Ids of the given comments that are team-only themselves or sit below a team-only one."""
    by_id = {c.id: c for c in comments}
    hidden: set[uuid.UUID] = set()

    def is_hidden(comment: Comment) -> bool:
        seen = []
        current = comment
        for _ in range(_MAX_WALK):
            if current.id in hidden or current.visibility == "internal":
                hidden.update(c.id for c in seen)
                hidden.add(current.id)
                return True
            seen.append(current)
            parent = by_id.get(current.parent_id) if current.parent_id else None
            if parent is None:
                return False
            current = parent
        return True

    for comment in comments:
        is_hidden(comment)
    return hidden
