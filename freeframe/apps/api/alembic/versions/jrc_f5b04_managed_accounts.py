"""JR Content: persistent managed accounts, including existing UUIDv5 identities."""

from alembic import op
import sqlalchemy as sa

revision = "jrc_f5b04"
down_revision = "e3a7c1d9b5f2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("jrc_managed", sa.Boolean(), nullable=False, server_default=sa.false()))
    # FreeFrame creates UUIDv4 users; only the JR Content synchronizer uses UUIDv5.
    # Also cover accounts which acquired a password through the old magic-code flow.
    op.execute("""
        UPDATE users SET jrc_managed = true, password_hash = NULL,
            invite_token = NULL, invite_token_expires_at = NULL,
            token_version = token_version + 1
        WHERE substring(id::text, 15, 1) = '5' AND NOT is_superadmin
    """)


def downgrade():
    op.drop_column("users", "jrc_managed")
