"""Idempotency-Key handling for ``POST /tickets``.

Correctness rests on the database unique constraint
``uq_idempotency_keys_key_actor_endpoint`` (``key`` + ``actor_id`` +
``endpoint``). Two concurrent requests with the same key race on that index:
exactly one insert wins and the other raises ``IntegrityError``, which the
caller treats as a hit and replays the winner's stored response. The winner's
whole transaction — ticket + audit + idempotency row — either commits
together or rolls back together, so a failed create can never leave a stale
idempotency record behind. No Python in-memory dictionary is involved.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IdempotencyKey

# Scoping the key to the actor is what lets two users share the same key value
# without interfering with each other.
IDEMPOTENCY_SCOPE_ATTRS = ("actor_id", "endpoint")


class IdempotencyReplay(Exception):
    """A previous request used the same key; carry its stored response."""

    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__("idempotency key already used")
        self.response = response


class IdempotencyConflict(Exception):
    """The key is already bound to a different request body."""

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__("idempotency key reused with different request")
        self.detail = detail


def request_hash(payload: dict[str, Any]) -> str:
    """Deterministic hash of the request body for the ``request_hash`` column."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_stored(db: Session, key: str, actor_id: UUID, endpoint: str) -> IdempotencyKey | None:
    return db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.key == key,
            IdempotencyKey.actor_id == actor_id,
            IdempotencyKey.endpoint == endpoint,
        )
    )


def begin_scope(
    db: Session,
    *,
    key: str,
    actor_id: UUID,
    endpoint: str,
    payload: dict[str, Any],
) -> IdempotencyKey | None:
    """Claim the idempotency key for this request.

    Returns the claimed row to save alongside the business write, or ``None``
    when the key is already taken — in which case either an
    ``IdempotencyReplay`` (same payload) or ``IdempotencyConflict`` (different
    payload) is raised so the caller can respond without creating anything.
    """
    existing = find_stored(db, key, actor_id, endpoint)
    if existing is not None:
        _raise_replay_or_conflict(existing, payload)
        return None

    record = IdempotencyKey(
        key=key,
        endpoint=endpoint,
        actor_id=actor_id,
        request_hash=request_hash(payload),
        response_json={},  # filled in by the caller before commit
    )
    db.add(record)
    return record


def _raise_replay_or_conflict(record: IdempotencyKey, payload: dict[str, Any]) -> None:
    if record.request_hash == request_hash(payload):
        raise IdempotencyReplay(dict(record.response_json))
    raise IdempotencyConflict(
        {
            "code": "conflict",
            "message": "该 Idempotency-Key 已被其他请求使用，请勿复用或更换 key",
        }
    )


def handle_integrity_error_on_claim(
    db: Session,
    *,
    key: str,
    actor_id: UUID,
    endpoint: str,
    payload: dict[str, Any],
) -> None:
    """Resolve a unique-constraint race after rollback.

    A concurrent request won the insert; the loser must roll back its own
    transaction and replay/conflict based on the winner's stored row. Under
    READ COMMITTED the loser only receives the IntegrityError after the
    winner has committed, so the winner is always visible here.
    """
    db.rollback()
    winner = find_stored(db, key, actor_id, endpoint)
    if winner is not None:
        _raise_replay_or_conflict(winner, payload)
        return
    # No winner visible. Never commit a bare idempotency row without the
    # business write it belongs to; surface a conflict the client can retry.
    raise IdempotencyConflict(
        {
            "code": "conflict",
            "message": "并发请求冲突，请稍后重试或更换 Idempotency-Key",
        }
    )
