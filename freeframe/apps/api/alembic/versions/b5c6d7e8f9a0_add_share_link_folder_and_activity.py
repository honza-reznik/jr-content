"""add share link folder support and activity tracking

Revision ID: b5c6d7e8f9a0
Revises: a2b3c4d5e6f7
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add folder sharing columns to share_links and asset_shares, create share_link_activity table."""

    # -- share_links: make asset_id nullable, add new columns --
    op.alter_column('share_links', 'asset_id', existing_type=sa.UUID(), nullable=True)
    op.add_column('share_links', sa.Column('folder_id', sa.UUID(), sa.ForeignKey('folders.id'), nullable=True))
    op.add_column('share_links', sa.Column('title', sa.String(255), nullable=False, server_default=''))
    op.add_column('share_links', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('share_links', sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('share_links', sa.Column('show_versions', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('share_links', sa.Column('show_watermark', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('share_links', sa.Column('appearance', sa.JSON(), nullable=False,
        server_default='{"layout":"grid","theme":"dark","accent_color":null,"open_in_viewer":true,"sort_by":"created_at"}'))
    op.create_index(op.f('ix_share_links_folder_id'), 'share_links', ['folder_id'], unique=False)
    op.create_check_constraint(
        'ck_share_link_asset_or_folder',
        'share_links',
        "(asset_id IS NOT NULL AND folder_id IS NULL) OR (asset_id IS NULL AND folder_id IS NOT NULL)"
    )

    # -- asset_shares: make asset_id nullable, add folder_id --
    op.alter_column('asset_shares', 'asset_id', existing_type=sa.UUID(), nullable=True)
    op.add_column('asset_shares', sa.Column('folder_id', sa.UUID(), sa.ForeignKey('folders.id'), nullable=True))
    op.create_index(op.f('ix_asset_shares_folder_id'), 'asset_shares', ['folder_id'], unique=False)
    op.create_check_constraint(
        'ck_asset_share_asset_or_folder',
        'asset_shares',
        "(asset_id IS NOT NULL AND folder_id IS NULL) OR (asset_id IS NULL AND folder_id IS NOT NULL)"
    )

    # -- share_link_activity table --
    share_activity_action = sa.Enum('opened', 'viewed_asset', 'commented', 'approved', 'rejected', 'downloaded',
                                     name='shareactivityaction')
    op.create_table('share_link_activity',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('share_link_id', sa.UUID(), nullable=False),
        sa.Column('action', share_activity_action, nullable=False),
        sa.Column('actor_email', sa.String(255), nullable=False),
        sa.Column('actor_name', sa.String(255), nullable=True),
        sa.Column('asset_id', sa.UUID(), nullable=True),
        sa.Column('asset_name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['share_link_id'], ['share_links.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_share_link_activity_share_link_id'), 'share_link_activity', ['share_link_id'], unique=False)
    op.create_index('ix_share_activity_link_created', 'share_link_activity',
                    ['share_link_id', sa.text('created_at DESC')])


def downgrade() -> None:
    """Reverse all changes."""
    # -- drop share_link_activity --
    op.drop_index('ix_share_activity_link_created', table_name='share_link_activity')
    op.drop_index(op.f('ix_share_link_activity_share_link_id'), table_name='share_link_activity')
    op.drop_table('share_link_activity')
    sa.Enum(name='shareactivityaction').drop(op.get_bind(), checkfirst=True)

    # -- asset_shares: revert --
    op.drop_constraint('ck_asset_share_asset_or_folder', 'asset_shares', type_='check')
    op.drop_index(op.f('ix_asset_shares_folder_id'), table_name='asset_shares')
    op.drop_column('asset_shares', 'folder_id')
    op.alter_column('asset_shares', 'asset_id', existing_type=sa.UUID(), nullable=False)

    # -- share_links: revert --
    op.drop_constraint('ck_share_link_asset_or_folder', 'share_links', type_='check')
    op.drop_index(op.f('ix_share_links_folder_id'), table_name='share_links')
    op.drop_column('share_links', 'appearance')
    op.drop_column('share_links', 'show_watermark')
    op.drop_column('share_links', 'show_versions')
    op.drop_column('share_links', 'is_enabled')
    op.drop_column('share_links', 'description')
    op.drop_column('share_links', 'title')
    op.drop_column('share_links', 'folder_id')
    op.alter_column('share_links', 'asset_id', existing_type=sa.UUID(), nullable=False)
