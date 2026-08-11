"""add reply lifecycle fields to ticket_replies

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-11 00:00:00.000000

W2-B introduces a reply lifecycle (draft -> reviewed -> sent) plus review and
send metadata. Existing rows (e.g. replies created by W2-A tests or manual
data) keep their current ``is_sent`` value and get ``status`` derived from it:
an already-sent reply becomes ``sent``, everything else stays ``draft``.

This preserves existing data: no rows are deleted or modified other than
filling the new ``status`` column from the old ``is_sent`` flag.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ticket_replies', sa.Column('status', sa.String(length=20),
                 server_default='draft', nullable=False))
    op.add_column('ticket_replies', sa.Column('reviewer_id', sa.UUID(), nullable=True))
    op.add_column('ticket_replies', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('ticket_replies', sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True))
    # Backfill: rows already flagged sent in W2-A become ``sent``.
    op.execute("UPDATE ticket_replies SET status = 'sent' WHERE is_sent = true")
    op.create_check_constraint('ck_ticket_replies_status',
                               'ticket_replies',
                               "status IN ('draft', 'reviewed', 'sent')")
    op.create_index('idx_ticket_replies_status', 'ticket_replies', ['status'])
    op.create_foreign_key('ticket_replies_reviewer_id_fkey',
                          'ticket_replies', 'users', ['reviewer_id'], ['id'],
                          ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('ticket_replies_reviewer_id_fkey', 'ticket_replies', type_='foreignkey')
    op.drop_index('idx_ticket_replies_status', table_name='ticket_replies')
    op.drop_constraint('ck_ticket_replies_status', 'ticket_replies', type_='check')
    op.drop_column('ticket_replies', 'sent_at')
    op.drop_column('ticket_replies', 'reviewed_at')
    op.drop_column('ticket_replies', 'reviewer_id')
    op.drop_column('ticket_replies', 'status')
