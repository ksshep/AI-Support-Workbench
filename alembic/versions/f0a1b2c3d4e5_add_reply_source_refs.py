"""add source_refs to ticket_replies

Revision ID: f0a1b2c3d4e5
Revises: e2f3a4b5c6d7
Create Date: 2026-08-12 00:00:00.000000

W4-A stores the minimal RAG source summary on AI-generated reply drafts so an
agent can see *why* the AI suggested this content without the full context
being persisted. ``source_refs`` is a nullable JSONB column: only
knowledge_item_id / title / chunk_index (and optionally page_number) are
stored — never the full RAG context, the prompt or the knowledge content.
Existing rows keep their data (the column is nullable).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ticket_replies',
        sa.Column('source_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ticket_replies', 'source_refs')
