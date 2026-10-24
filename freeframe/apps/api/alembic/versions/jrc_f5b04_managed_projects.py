"""JR Content: mark projects, including existing synchronized UUIDv5 projects."""

from alembic import op
import sqlalchemy as sa

revision = "jrc_f5b04_projects"
down_revision = "jrc_f5b04"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("jrc_managed", sa.Boolean(), nullable=False, server_default=sa.false()))
    # FreeFrame creates UUIDv4 projects; the JR Content synchronizer uses UUIDv5.
    # Mark old projects too, so their existing public share links become private.
    op.execute("UPDATE projects SET jrc_managed = true WHERE substring(id::text, 15, 1) = '5'")


def downgrade():
    op.drop_column("projects", "jrc_managed")
