"""add file_content and updated_at to knowledge_items

Revision ID: e2f3a4b5c6d7
Revises: c7d8e9f0a1b2
Create Date: 2026-08-12 00:00:00.000000

W3-B stores the raw uploaded bytes on ``knowledge_items`` so the RQ worker
(a separate Docker container with no shared filesystem with the web process)
can read the original file straight from the database instead of relying on
a volume mount. This migration also adds ``updated_at`` to match the
``created_at`` convention used by the other tables.

Existing rows keep their data: ``file_content`` is nullable and stays NULL for
documents uploaded before this migration, and ``updated_at`` is backfilled to
``created_at``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('knowledge_items', sa.Column('file_content', sa.LargeBinary(), nullable=True))
    op.add_column('knowledge_items', sa.Column('updated_at', sa.DateTime(timezone=True),
                 server_default=sa.text('now()'), nullable=False))
    # Backfill updated_at for existing rows (they were all created at some point).
    op.execute("UPDATE knowledge_items SET updated_at = created_at")
    op.add_column('knowledge_chunks', sa.Column('page_number', sa.Integer(), nullable=True))
    op.create_index('idx_knowledge_chunks_item_id', 'knowledge_chunks', ['knowledge_item_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_knowledge_chunks_item_id', table_name='knowledge_chunks')
    op.drop_column('knowledge_chunks', 'page_number')
    op.drop_column('knowledge_items', 'updated_at')
    op.drop_column('knowledge_items', 'file_content')
