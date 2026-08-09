"""scope idempotency keys per actor and endpoint

Revision ID: a1b2c3d4e5f6
Revises: d990239d5de5
Create Date: 2026-08-09 00:00:00.000000

The original unique constraint was on ``key`` alone, which made one actor's
key block every other actor from ever using the same value. W2-A requires
"different users may reuse the same key without interference", so the
constraint becomes (``key``, ``actor_id``, ``endpoint``).

Data preservation: dropping a unique constraint never deletes rows; existing
idempotency records keep their values. Any accidental duplicates across
actors that were impossible before remain impossible now because the old
unique constraint already forbade duplicate ``key`` values.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd990239d5de5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('uq_idempotency_keys_key', 'idempotency_keys', type_='unique')
    op.create_unique_constraint(
        'uq_idempotency_keys_key_actor_endpoint',
        'idempotency_keys',
        ['key', 'actor_id', 'endpoint'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_idempotency_keys_key_actor_endpoint', 'idempotency_keys', type_='unique'
    )
    op.create_unique_constraint(
        'uq_idempotency_keys_key', 'idempotency_keys', ['key']
    )
