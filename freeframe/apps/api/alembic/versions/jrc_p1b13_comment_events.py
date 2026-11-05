"""JR Content: persist pending video comment notifications."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "jrc_p1b13_comment_events"
down_revision = "jrc_f5b04_projects"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jrc_comment_events",
        sa.Column(
            "comment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("comments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_jrc_comment_events_next_attempt_at",
        "jrc_comment_events",
        ["next_attempt_at"],
    )


def downgrade():
    op.drop_table("jrc_comment_events")
