"""add visibility to comments

Revision ID: 4094df400c86
Revises: c8d9e2f1a3b4
Create Date: 2026-03-20 07:33:53.357854

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4094df400c86'
down_revision: Union[str, Sequence[str], None] = 'c8d9e2f1a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('comments', sa.Column('visibility', sa.String(length=20), server_default='public', nullable=False))
    # Drop orphaned FK constraints (tables managed outside alembic models)
    op.drop_constraint('activity_logs_org_id_fkey', 'activity_logs', type_='foreignkey')
    op.drop_constraint('asset_shares_shared_with_team_id_fkey', 'asset_shares', type_='foreignkey')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_foreign_key('asset_shares_shared_with_team_id_fkey', 'asset_shares', 'teams', ['shared_with_team_id'], ['id'])
    op.create_foreign_key('activity_logs_org_id_fkey', 'activity_logs', 'organizations', ['org_id'], ['id'])
    op.drop_column('comments', 'visibility')
