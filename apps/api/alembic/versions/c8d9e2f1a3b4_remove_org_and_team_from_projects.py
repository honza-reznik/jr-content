"""remove org and team from projects

Revision ID: c8d9e2f1a3b4
Revises: 07ae25f4f72f
Create Date: 2026-03-19 19:57:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c8d9e2f1a3b4'
down_revision = '07ae25f4f72f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop org_id and team_id columns from projects table
    op.drop_column('projects', 'org_id')
    op.drop_column('projects', 'team_id')


def downgrade() -> None:
    # Add back org_id and team_id columns
    op.add_column('projects', sa.Column('team_id', postgresql.UUID(), nullable=True))
    op.add_column('projects', sa.Column('org_id', postgresql.UUID(), nullable=False))
