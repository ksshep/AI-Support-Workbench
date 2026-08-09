"""Unified audit-log writer.

``audit_logs`` is append-only by contract: this module is the only writer and
it never updates or deletes existing rows, so history cannot be rewritten
through the API. The write happens on the caller's session, so it commits
atomically with the business change that produced it.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from ..models import AuditLog


def create_audit_log(
    db: Session,
    *,
    actor_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Append one audit row and return it.

    ``old_value`` / ``new_value`` are stored as JSONB. Callers must pass only
    safe snapshots — never passwords, tokens, API keys or full sensitive
    text. ``entity_type`` is the model name (``ticket``, ``user`` ...) and
    ``entity_id`` its primary key.
    """
    log = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
    )
    db.add(log)
    return log
