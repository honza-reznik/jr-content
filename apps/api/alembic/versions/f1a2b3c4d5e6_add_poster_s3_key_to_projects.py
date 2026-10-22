"""add poster_s3_key to projects

Revision ID: f1a2b3c4d5e6
Revises: 39ca58559bbc
Create Date: 2026-03-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '39ca58559bbc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('poster_s3_key', sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'poster_s3_key')
