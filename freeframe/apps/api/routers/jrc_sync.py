"""Interní synchronizace JR Content; bez pozvánek, hesel a e-mailových úloh."""

import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.project import Project, ProjectMember, ProjectRole, ProjectType
from ..models.user import User, UserStatus
from ..schemas.auth import DisplayName

router = APIRouter(prefix="/jrc-sync", tags=["jrc-sync"])
# Stabilní jmenný prostor; změna by přerušila vazby při obnově ze zálohy.
NAMESPACE = uuid.UUID("29c4ee9e-ab19-48c4-bb44-2fc77109b234")


def require_sync_user(current_user: User = Depends(get_current_user)):
    email = os.environ.get("JRC_FF_SYNC_EMAIL", "")
    if not email or not current_user.is_superadmin or current_user.email.lower() != email.lower():
        raise HTTPException(status_code=403, detail="JR Content service account required")
    return current_user


def remote_id(kind, source_id):
    return uuid.uuid5(NAMESPACE, f"{kind}:{source_id}")


def lock_identity(db, identity):
    # Transaction lock also covers the first insert (no existing row to lock yet).
    key = int.from_bytes(identity.bytes[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


FORBIDDEN_EMAIL_CHARS = set(',;<>"')


def _require_plain_email(value: str) -> str:
    # Brightbean already validated the address. EmailStr would reject the
    # reserved test domains (example.invalid, .test, .local) used by local demo data,
    # so only the basic shape is checked here. Lower case prevents Ella@ vs ella@ duplicates.
    if value.count("@") != 1 or any(
        c.isspace() or ord(c) < 32 or ord(c) == 127 or c in FORBIDDEN_EMAIL_CHARS for c in value
    ):
        raise ValueError("Invalid email address")
    local, domain = value.split("@")
    if not local or not domain:
        raise ValueError("Invalid email address")
    return value.lower()


SyncEmail = Annotated[str, Field(min_length=3, max_length=255), AfterValidator(_require_plain_email)]


class AccountRequest(BaseModel):
    email: SyncEmail
    name: DisplayName
    is_active: bool


class ProjectRequest(BaseModel):
    name: DisplayName


@router.put("/accounts/{source_id}")
def sync_account(
    source_id: uuid.UUID,
    body: AccountRequest,
    db: Session = Depends(get_db),
    service_user: User = Depends(require_sync_user),
):
    identity = remote_id("account", source_id)
    lock_identity(db, identity)
    user = db.query(User).filter(User.id == identity).first()
    if user and (user.deleted_at is not None or user.is_superadmin):
        raise HTTPException(status_code=409, detail="Managed account is deleted or privileged")
    # Never adopt another identity just because it has the requested e-mail (any letter case).
    if db.query(User).filter(func.lower(User.email) == body.email, User.id != identity).first():
        raise HTTPException(status_code=409, detail="Email belongs to another account")
    if user is None:
        user = User(id=identity, is_superadmin=False, password_hash=None)
        db.add(user)
    user.email = body.email
    user.name = body.name
    user.email_verified = True
    user.status = UserStatus.active if body.is_active else UserStatus.deactivated
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Never adopt another identity just because it has the requested e-mail.
        raise HTTPException(status_code=409, detail="Email belongs to another account") from None
    return {"id": str(identity)}


@router.post("/accounts/{source_id}/deactivate")
def deactivate_account(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    service_user: User = Depends(require_sync_user),
):
    """Deactivate a managed account by its derived identity; never creates one."""
    identity = remote_id("account", source_id)
    lock_identity(db, identity)
    user = db.query(User).filter(User.id == identity).first()
    if user is None or user.deleted_at is not None:
        return {"id": str(identity), "found": False}
    if user.is_superadmin:
        raise HTTPException(status_code=409, detail="Managed account is privileged")
    user.status = UserStatus.deactivated
    db.commit()
    return {"id": str(identity), "found": True}


@router.put("/projects/{source_id}")
def sync_project(
    source_id: uuid.UUID,
    body: ProjectRequest,
    db: Session = Depends(get_db),
    service_user: User = Depends(require_sync_user),
):
    identity = remote_id("project", source_id)
    lock_identity(db, identity)
    project = db.query(Project).filter(Project.id == identity).first()
    if project and (project.deleted_at is not None or project.created_by != service_user.id):
        raise HTTPException(status_code=409, detail="Managed project is deleted or owned by another account")
    if project is None:
        project = Project(id=identity, name=body.name, created_by=service_user.id, project_type=ProjectType.team)
        db.add(project)
        db.flush()
    project.name = body.name
    project.is_public = False
    owner = db.query(ProjectMember).filter(
        ProjectMember.project_id == identity, ProjectMember.user_id == service_user.id,
    ).first()
    if owner is None:
        owner = ProjectMember(project_id=identity, user_id=service_user.id)
        db.add(owner)
    owner.role = ProjectRole.owner
    owner.deleted_at = None
    db.commit()
    return {"id": str(identity), "service_user_id": str(service_user.id)}
