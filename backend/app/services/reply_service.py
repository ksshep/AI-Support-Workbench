"""Reply business logic: create draft, list, review, send (W2-B).

Reply lifecycle is ``draft -> reviewed -> sent`` and is persisted in the
``ticket_replies.status`` column (never in memory). Sending a reviewed reply
also fires the ticket state machine's ``reply`` event (``in_review ->
replied``); the reply change, the ticket change and every audit row commit in
a single transaction so a partial send can never be observed.

Replies that are not ``sent`` are invisible to customers — enforced at query
time by filtering ``status == 'sent'`` for customer roles.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ticket, TicketReply, User
from ..schemas.reply import ReplyCreate
from . import audit as audit_service
from .state_machine import check_role_can_transition, next_status

# Replies must only be created on tickets that are still being worked on.
REPLY_ALLOWED_TICKET_STATUSES = ("open", "in_review", "replied")


def _get_ticket_or_404(db: Session, ticket_id: UUID) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id))
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "工单不存在"},
        )
    return ticket


def _get_reply_or_404(db: Session, reply_id: UUID, ticket_id: UUID) -> TicketReply:
    """Fetch a reply and reject the common not-found / wrong-ticket cases."""
    reply = db.scalar(
        select(TicketReply).where(
            TicketReply.id == reply_id, TicketReply.ticket_id == ticket_id
        )
    )
    if reply is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "回复不存在或不属于该工单"},
        )
    return reply


def _ensure_staff(current_user: User) -> None:
    if current_user.role not in ("agent", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": "当前角色无权进行回复操作",
            },
        )


def _ensure_customer_owns_ticket(current_user: User, ticket: Ticket) -> None:
    if current_user.role == "customer" and ticket.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "无权访问该工单"},
        )


def _serialize_reply(reply: TicketReply, sender_name: str = "") -> dict:
    return {
        "id": str(reply.id),
        "ticket_id": str(reply.ticket_id),
        "content": reply.content,
        "status": reply.status,
        "is_ai_suggestion": reply.is_ai_suggestion,
        "sender_id": str(reply.sender_id),
        "sender_name": sender_name,
        "reviewer_id": str(reply.reviewer_id) if reply.reviewer_id else None,
        "reviewed_at": reply.reviewed_at.isoformat() if reply.reviewed_at else None,
        "sent_at": reply.sent_at.isoformat() if reply.sent_at else None,
        "created_at": reply.created_at.isoformat(),
    }


def create_reply(
    db: Session,
    *,
    ticket_id: UUID,
    body: ReplyCreate,
    current_user: User,
    ip_address: str | None = None,
) -> dict:
    """Create a ``draft`` reply as an agent/admin.

    sender_id always comes from the JWT user; the schema forbids smuggling it
    via the request body. Audit stores only a length summary, never the full
    reply content.
    """
    _ensure_staff(current_user)
    ticket = _get_ticket_or_404(db, ticket_id)

    if ticket.status not in REPLY_ALLOWED_TICKET_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_state_transition",
                "message": f"工单状态 {ticket.status} 不允许新增回复",
                "current_status": ticket.status,
            },
        )

    reply = TicketReply(
        ticket_id=ticket.id,
        sender_id=current_user.id,
        content=body.content.strip(),
        status="draft",
        is_sent=False,
    )
    db.add(reply)
    db.flush()

    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="reply.created",
        entity_type="ticket",
        entity_id=ticket.id,
        new_value={
            "reply_id": str(reply.id),
            "content_length": len(reply.content),
            "status": "draft",
        },
        ip_address=ip_address,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(reply)
    return _serialize_reply(reply, sender_name=current_user.name)


def list_replies(
    db: Session, *, ticket_id: UUID, current_user: User
) -> dict[str, list[dict]]:
    """List replies for a ticket.

    Customers only see ``sent`` replies on their own ticket; staff see every
    reply. Ordered by ``created_at`` ascending.
    """
    ticket = _get_ticket_or_404(db, ticket_id)
    _ensure_customer_owns_ticket(current_user, ticket)

    query = select(TicketReply).where(TicketReply.ticket_id == ticket.id)
    if current_user.role == "customer":
        query = query.where(TicketReply.status == "sent")
    query = query.order_by(TicketReply.created_at.asc())
    replies = db.scalars(query).all()

    # Resolve sender names in one query.
    from ..models import User as UserModel

    sender_ids = list({r.sender_id for r in replies})
    names = {}
    if sender_ids:
        rows = db.execute(
            select(UserModel.id, UserModel.name).where(UserModel.id.in_(sender_ids))
        ).all()
        names = {str(uid): name for uid, name in rows}

    return {
        "items": [_serialize_reply(r, sender_name=names.get(str(r.sender_id), ""))
                  for r in replies]
    }


def review_reply(
    db: Session,
    *,
    ticket_id: UUID,
    reply_id: UUID,
    approved: bool,
    current_user: User,
    ip_address: str | None = None,
) -> dict:
    """Approve (draft -> reviewed) or reject (stays draft) a reply.

    Only agent/admin may review. ``sent`` replies are terminal and cannot be
    re-reviewed. Approval never auto-sends.
    """
    _ensure_staff(current_user)
    ticket = _get_ticket_or_404(db, ticket_id)
    reply = _get_reply_or_404(db, reply_id, ticket_id)

    if reply.status == "sent":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conflict", "message": "已发送的回复不能再次审核"},
        )
    if reply.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "conflict",
                "message": f"当前回复状态 {reply.status} 不允许审核",
            },
        )

    old_status = reply.status
    if approved:
        reply.status = "reviewed"
        reply.reviewer_id = current_user.id
        reply.reviewed_at = datetime.now(timezone.utc)
        action = "reply.approved"
    else:
        # Rejected reply stays draft; only the decision is recorded.
        action = "reply.rejected"
        # Clear any half-applied review fields from a previous review attempt.
        reply.reviewer_id = None
        reply.reviewed_at = None

    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action=action,
        entity_type="ticket",
        entity_id=ticket.id,
        old_value={"reply_id": str(reply.id), "status": old_status},
        new_value={
            "reply_id": str(reply.id),
            "status": reply.status,
            "approved": approved,
            "content_length": len(reply.content),
        },
        ip_address=ip_address,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(reply)
    return {
        "id": str(reply.id),
        "ticket_id": str(ticket.id),
        "status": reply.status,
        "approved": approved,
        "reviewer_id": str(reply.reviewer_id) if reply.reviewer_id else None,
        "reviewed_at": reply.reviewed_at.isoformat() if reply.reviewed_at else None,
    }


def send_reply(
    db: Session,
    *,
    ticket_id: UUID,
    reply_id: UUID,
    current_user: User,
    ip_address: str | None = None,
) -> dict:
    """Send a reviewed reply and drive the ticket state machine to ``replied``.

    Everything — reply status/sent_at, the ``in_review -> replied`` ticket
    transition, and both audit rows — commits in one transaction. The ticket
    row is locked (``FOR UPDATE``) so two concurrent sends cannot both
    transition it.
    """
    _ensure_staff(current_user)
    # Lock the ticket so the state transition is race-free.
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
    reply = _get_reply_or_404(db, reply_id, ticket_id)

    if reply.status == "sent":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conflict", "message": "该回复已经发送，不能重复发送"},
        )
    if reply.status != "reviewed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_input",
                "message": "只有审核通过的回复才能发送",
                "reply_status": reply.status,
            },
        )

    # Reply becomes sent.
    reply.status = "sent"
    reply.is_sent = True
    reply.sent_at = datetime.now(timezone.utc)

    # Fire the ticket state machine: in_review -> replied.
    old_ticket_status = ticket.status
    new_ticket_status: str | None = None
    if old_ticket_status == "in_review":
        check_role_can_transition(current_user.role, old_ticket_status, "reply")
        new_ticket_status = next_status(old_ticket_status, "reply")
        ticket.status = new_ticket_status

    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="reply.sent",
        entity_type="ticket",
        entity_id=ticket.id,
        old_value={"reply_id": str(reply.id), "status": "reviewed"},
        new_value={
            "reply_id": str(reply.id),
            "status": "sent",
            "content_length": len(reply.content),
        },
        ip_address=ip_address,
    )
    if new_ticket_status is not None:
        audit_service.create_audit_log(
            db,
            actor_id=current_user.id,
            action="ticket.status_changed",
            entity_type="ticket",
            entity_id=ticket.id,
            old_value={"status": old_ticket_status},
            new_value={"status": new_ticket_status, "event": "reply"},
            ip_address=ip_address,
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(reply)
    db.refresh(ticket)

    return {
        "reply_id": str(reply.id),
        "ticket_id": str(ticket.id),
        "reply_status": reply.status,
        "ticket_status": ticket.status,
        "sent_at": reply.sent_at.isoformat(),
    }
