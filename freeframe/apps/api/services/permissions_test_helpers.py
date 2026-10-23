"""Test helpers for setting up permission fixtures in tests."""
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ..models.organization import Organization, OrgMember, OrgRole
from ..models.project import Project, ProjectMember, ProjectRole, ProjectType
from ..models.user import User, UserStatus


def create_test_user(db: Session, email: str = None, name: str = "Test User") -> User:
    user = User(
        email=email or f"user-{uuid.uuid4()}@test.com",
        name=name,
        status=UserStatus.active,
        password_hash="$2b$12$fakehashfortest",
    )
    db.add(user)
    db.flush()
    return user


def create_test_org(db: Session, owner: User, name: str = "Test Org") -> Organization:
    slug = f"test-org-{uuid.uuid4().hex[:8]}"
    org = Organization(name=name, slug=slug)
    db.add(org)
    db.flush()
    member = OrgMember(org_id=org.id, user_id=owner.id, role=OrgRole.owner, joined_at=datetime.now(timezone.utc))
    db.add(member)
    db.flush()
    return org


def create_test_project(db: Session, org: Organization, owner: User, name: str = "Test Project") -> Project:
    project = Project(
        org_id=org.id,
        name=name,
        project_type=ProjectType.personal,
        created_by=owner.id,
    )
    db.add(project)
    db.flush()
    member = ProjectMember(project_id=project.id, user_id=owner.id, role=ProjectRole.owner)
    db.add(member)
    db.flush()
    return project
