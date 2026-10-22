"""merge heads

Revision ID: 91065b39168e
Revises: 4094df400c86, a11ce9b821a0
Create Date: 2026-03-20 08:57:50.768948

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91065b39168e'
down_revision: Union[str, Sequence[str], None] = ('4094df400c86', 'a11ce9b821a0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
