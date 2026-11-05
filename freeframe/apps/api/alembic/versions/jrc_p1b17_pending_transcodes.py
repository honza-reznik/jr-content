"""Durable conversion dispatch after completed uploads."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "jrc_p1b17_pending_transcodes"
down_revision = "jrc_p1b13_comment_events"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("asset_versions", sa.Column("processing_recovery_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "pending_transcodes",
        sa.Column("version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("asset_versions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("pending_transcodes")
    op.drop_column("asset_versions", "processing_recovery_attempts")
