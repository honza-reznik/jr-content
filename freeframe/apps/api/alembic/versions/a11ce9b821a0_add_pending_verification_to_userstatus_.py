"""add pending_verification to userstatus enum

Revision ID: a11ce9b821a0
Revises: 07ae25f4f72f
Create Date: 2026-03-20 08:47:54.834468

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a11ce9b821a0'
down_revision: Union[str, Sequence[str], None] = '07ae25f4f72f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'pending_verification'")


def downgrade() -> None:
    pass
