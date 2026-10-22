"""add folders table and asset folder_id

Revision ID: 39ca58559bbc
Revises: 91065b39168e
Create Date: 2026-03-20 10:52:32.128816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '39ca58559bbc'
down_revision: Union[str, Sequence[str], None] = '91065b39168e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('folders',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['parent_id'], ['folders.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_folders_parent_id'), 'folders', ['parent_id'], unique=False)
    op.create_index('ix_folders_project_deleted', 'folders', ['project_id', 'deleted_at'], unique=False)
    op.create_index(op.f('ix_folders_project_id'), 'folders', ['project_id'], unique=False)
    op.create_index('ix_folders_project_parent', 'folders', ['project_id', 'parent_id'], unique=False)
    op.create_index('uq_folder_name_per_parent', 'folders', ['project_id', 'parent_id', 'name'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.add_column('assets', sa.Column('folder_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_assets_folder_id'), 'assets', ['folder_id'], unique=False)
    op.create_index('ix_assets_project_folder_deleted', 'assets', ['project_id', 'folder_id', 'deleted_at'], unique=False)
    op.create_foreign_key(None, 'assets', 'folders', ['folder_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(None, 'assets', type_='foreignkey')
    op.drop_index('ix_assets_project_folder_deleted', table_name='assets')
    op.drop_index(op.f('ix_assets_folder_id'), table_name='assets')
    op.drop_column('assets', 'folder_id')
    op.drop_index('uq_folder_name_per_parent', table_name='folders', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_folders_project_parent', table_name='folders')
    op.drop_index(op.f('ix_folders_project_id'), table_name='folders')
    op.drop_index('ix_folders_project_deleted', table_name='folders')
    op.drop_index(op.f('ix_folders_parent_id'), table_name='folders')
    op.drop_table('folders')
