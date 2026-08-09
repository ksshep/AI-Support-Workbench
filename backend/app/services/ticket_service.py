"""Ticket business logic: create / list / detail / update / transition.

Permissions are enforced here, not in the route layer:
- ``customer`` sees and edits only their own tickets;
- ``agent`` and ``admin`` see all tickets and may run staff actions;
- only ``customer`` may create tickets via this API;
- status changes go exclusively through the state machine; PATCH cannot touch
  ``status`` (the field is not even accepted by the Pydantic schema).

All writes happen on the SQLAlchemy session the caller passed in, and every
successful write appends an audit log in the same transaction, so a business
change and its audit trail commit or roll back together.
"""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AuditLog, Ticket, TicketReply, User
from ..schemas.ticket import TicketCreate, TicketUpdate
from . import audit as audit_service
from . import idempotency as idempotency_service
from .state_machine import (
    VALID_EVENTS,
    ForbiddenTransitionError,
    InvalidTransitionError,
    allowed_events,
    check_role_can_transition,
    next_status,
)

END_POINT = "POST /tickets"
IDEMPOTENCY_KEY_LIMIT = 128


# Staff see every ticket; customers are scoped to their own rows.
def _ticket_scope_filter(current_user: User):
    if current_user.role == "customer":
        return Ticket.customer_id == current_user.id
    return None


def _get_ticket_or_404(db: Session, ticket_id: UUID) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id))
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "工单不存在"},
        )
    return ticket


def _ensure_can_view(current_user: User, ticket: Ticket) -> None:
    if current_user.role == "customer" and ticket.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "无权访问该工单"},
        )


def _serialize_create_response(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": str(ticket.id),
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
        "classification": ticket.classification,
        "summary": ticket.summary,
        "sentiment": ticket.sentiment,
        "created_at": ticket.created_at.isoformat(),
    }


def create_ticket(
    db: Session,
    *,
    body: TicketCreate,
    current_user: User,
    idempotency_key: str | None,
    ip_address: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Create a ticket as the current customer, honoring Idempotency-Key.

    Returns ``(response_payload, http_status)``. On an idempotent replay the
    stored response snapshot is returned with ``200`` and nothing is written.
    """
    # Step 0 — role gate: only customers may create tickets here.
    if current_user.role != "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "仅客户可以提交工单"},
        )

    payload = body.model_dump(exclude_none=True)

    # Step 1 — idempotency: claim the key before doing any work.
    claim = None
    if idempotency_key is not None:
        claim = idempotency_service.begin_scope(
            db,
            key=idempotency_key,
            actor_id=current_user.id,
            endpoint=END_POINT,
            payload=payload,
        )

    # Step 2 — build the ticket. customer_id always comes from the token,
    # never from the request body (the schema has no such field).
    ticket = Ticket(
        customer_id=current_user.id,
        title=body.title,
        description=body.description,
        status="open",
        classification=body.classification or "",
    )

    # Step 3 — write ticket + audit log + idempotency row in one transaction.
    db.add(ticket)
    # Flush so DB server defaults (created_at, updated_at) are loaded before
    # we serialize the response snapshot for the audit log and idempotency row.
    db.flush()
    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="ticket.created",
        entity_type="ticket",
        entity_id=ticket.id,
        new_value=_serialize_create_response(ticket),
        ip_address=ip_address,
    )
    response_payload = _serialize_create_response(ticket)
    if claim is not None:
        claim.response_json = response_payload

    try:
        db.commit()
    except IntegrityError:
        # Unique-constraint race: a concurrent request claimed the same key
        # first. Roll back this loser's writes and replay the winner.
        if idempotency_key is not None:
            db.rollback()
            idempotency_service.handle_integrity_error_on_claim(
                db,
                key=idempotency_key,
                actor_id=current_user.id,
                endpoint=END_POINT,
                payload=payload,
            )
            db.commit()
            return response_payload, status.HTTP_201_CREATED
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(ticket)

    return response_payload, status.HTTP_201_CREATED


def list_tickets(
    db: Session,
    *,
    current_user: User,
    page: int,
    page_size: int,
    status_filter: str | None,
    priority_filter: str | None,
    classification_filter: str | None,
) -> dict[str, Any]:
    """Return a paginated, role-scoped ticket list.

    Filtering and pagination happen in SQL (LIMIT/OFFSET/COUNT), never by
    loading all rows into Python and slicing.
    """
    query = select(Ticket).order_by(Ticket.created_at.desc())
    count_query = select(func.count(Ticket.id))

    scope = _ticket_scope_filter(current_user)
    if scope is not None:
        query = query.where(scope)
        count_query = count_query.where(scope)
    if status_filter is not None:
        query = query.where(Ticket.status == status_filter)
        count_query = count_query.where(Ticket.status == status_filter)
    if priority_filter is not None:
        query = query.where(Ticket.priority == priority_filter)
        count_query = count_query.where(Ticket.priority == priority_filter)
    if classification_filter is not None:
        query = query.where(Ticket.classification == classification_filter)
        count_query = count_query.where(Ticket.classification == classification_filter)

    total = db.scalar(count_query) or 0
    rows = db.scalars(
        query.limit(page_size).offset((page - 1) * page_size)
    ).all()

    reply_counts = _reply_counts(db, [t.id for t in rows])
    names = _user_names(db, [t.customer_id for t in rows] + [t.assignee_id for t in rows if t.assignee_id])

    items = [
        {
            "id": str(t.id),
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "classification": t.classification,
            "summary": t.summary,
            "sentiment": t.sentiment,
            "customer_name": names.get(str(t.customer_id), ""),
            "assignee_name": names.get(str(t.assignee_id)) if t.assignee_id else None,
            "reply_count": reply_counts.get(str(t.id), 0),
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
        }
        for t in rows
    ]
    pages = max(1, -(-total // page_size)) if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def _reply_counts(db: Session, ticket_ids: list[UUID]) -> dict[str, int]:
    if not ticket_ids:
        return {}
    rows = db.execute(
        select(TicketReply.ticket_id, func.count(TicketReply.id))
        .where(TicketReply.ticket_id.in_(ticket_ids))
        .group_by(TicketReply.ticket_id)
    ).all()
    return {str(tid): cnt for tid, cnt in rows}


def _user_names(db: Session, user_ids: list[UUID]) -> dict[str, str]:
    ids = list(dict.fromkeys(uid for uid in user_ids if uid is not None))
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()
    return {str(uid): name for uid, name in rows}


def get_ticket_detail(db: Session, *, current_user: User, ticket_id: UUID) -> dict[str, Any]:
    """Return the full detail payload: ticket + replies + audit summary."""
    ticket = _get_ticket_or_404(db, ticket_id)
    _ensure_can_view(current_user, ticket)

    replies = db.scalars(
        select(TicketReply)
        .where(TicketReply.ticket_id == ticket.id)
        .order_by(TicketReply.created_at.asc())
    ).all()
    audit_rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "ticket", AuditLog.entity_id == ticket.id)
        .order_by(AuditLog.created_at.asc())
    ).all()

    sender_ids = [r.sender_id for r in replies]
    # Include the ticket owner and assignee in the name lookup even when they
    # have no replies, so customer_name / assignee_name are never blank.
    lookup_ids = [ticket.customer_id] + sender_ids
    if ticket.assignee_id is not None:
        lookup_ids.append(ticket.assignee_id)
    names = _user_names(db, lookup_ids)

    return {
        "id": str(ticket.id),
        "title": ticket.title,
        "description": ticket.description,
        "status": ticket.status,
        "priority": ticket.priority,
        "classification": ticket.classification,
        "summary": ticket.summary,
        "sentiment": ticket.sentiment,
        "customer_id": str(ticket.customer_id),
        "customer_name": names.get(str(ticket.customer_id), ""),
        "assignee_id": str(ticket.assignee_id) if ticket.assignee_id else None,
        "assignee_name": names.get(str(ticket.assignee_id)) if ticket.assignee_id else None,
        "replies": [
            {
                "id": str(r.id),
                "content": r.content,
                "is_ai_suggestion": r.is_ai_suggestion,
                "is_sent": r.is_sent,
                "sender_name": names.get(str(r.sender_id), ""),
                "created_at": r.created_at.isoformat(),
            }
            for r in replies
        ],
        "audit": [
            {
                "action": log.action,
                "old_value": log.old_value,
                "new_value": log.new_value,
                "created_at": log.created_at.isoformat(),
            }
            for log in audit_rows
        ],
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }


def _has_changes(ticket: Ticket, data: dict[str, Any]) -> bool:
    """Return whether applying ``data`` would change the ticket at all.

    Called against the *pre-update* attribute values so an unchanged PATCH
    (same value as stored) can skip both the write and the audit entry.
    """
    for field, value in data.items():
        if getattr(ticket, field) != value:
            return True
    return False


def update_ticket(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    body: TicketUpdate,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Apply a PATCH. Field-level permission rules decide what may change.

    - customer: only ``title`` / ``description``, only on their own ``open``
      ticket;
    - agent / admin: ``priority`` / ``classification`` (and title/description);
    - nobody may change ``status`` here — the schema does not accept it and
      the state machine owns status changes.
    """
    ticket = _get_ticket_or_404(db, ticket_id)
    _ensure_can_view(current_user, ticket)

    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "请求体不能为空"},
        )

    # Reject fields this role may not touch up front so an unauthorized PATCH
    # returns 403 rather than being silently ignored.
    if current_user.role == "customer":
        if "priority" in data or "classification" in data:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message": "客户只能修改标题和描述，不能修改优先级或分类",
                },
            )
        if ticket.status != "open":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "invalid_state_transition",
                    "message": "只有 open 状态的工单可以修改",
                },
            )

    # Capture the pre-update snapshot and detect "no real change" *before*
    # mutating, so a same-value PATCH skips the write and its audit entry.
    old_value = {field: getattr(ticket, field) for field in data}
    if not _has_changes(ticket, data):
        return _serialize_update_response(ticket)

    for field, value in data.items():
        setattr(ticket, field, value)

    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="ticket.updated",
        entity_type="ticket",
        entity_id=ticket.id,
        old_value=old_value,
        new_value=data,
        ip_address=ip_address,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(ticket)
    return _serialize_update_response(ticket)


def _serialize_update_response(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": str(ticket.id),
        "title": ticket.title,
        "description": ticket.description,
        "status": ticket.status,
        "priority": ticket.priority,
        "classification": ticket.classification,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }


def transition_ticket(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    event: str,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Fire a state-machine event. Locking + audit happen in one transaction."""
    ticket = (
        db.scalars(
            select(Ticket).where(Ticket.id == ticket_id).with_for_update()
        ).first()
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "工单不存在"},
        )
    _ensure_can_view(current_user, ticket)

    old_status = ticket.status
    # 1. Unknown event -> 400 invalid_input, independent of role/status.
    if event not in VALID_EVENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_input",
                "message": f"未知事件 '{event}'，合法事件: {', '.join(VALID_EVENTS)}",
                "current_status": old_status,
                "allowed_events": allowed_events(old_status),
            },
        )

    # 2. Role gate: a customer firing an agent-only event is a permission
    #    failure (403), not an invalid transition (400).
    try:
        check_role_can_transition(current_user.role, old_status, event)
    except ForbiddenTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": str(exc)},
        ) from exc

    # 3. Transition table: legal roles on an illegal (status, event) pair.
    try:
        new_status = next_status(old_status, event)
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_state_transition",
                "message": str(exc),
                "current_status": old_status,
                "allowed_events": exc.allowed,
            },
        ) from exc

    # Locked rows cannot change status here; database CHECK is the backstop.
    ticket.status = new_status
    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="ticket.status_changed",
        entity_type="ticket",
        entity_id=ticket.id,
        old_value={"status": old_status},
        new_value={"status": new_status, "event": event},
        ip_address=ip_address,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(ticket)

    return {
        "id": str(ticket.id),
        "status": ticket.status,
        "allowed_events": allowed_events(ticket.status),
        "updated_at": ticket.updated_at.isoformat(),
    }
