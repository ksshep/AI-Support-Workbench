"""Evaluation business logic (W2-B).

A customer may rate their own ``closed`` ticket exactly once. The
``UNIQUE(ticket_id)`` database constraint is the concurrency backstop: two
simultaneous evaluations for the same ticket race on it, one wins and the
other raises ``IntegrityError`` which we convert into a clear 409. No memory
dictionary is involved.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Evaluation, Ticket
from ..schemas.evaluation import EvaluationCreate
from . import audit as audit_service


def _get_ticket_or_404(db: Session, ticket_id: UUID) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id))
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "工单不存在"},
        )
    return ticket


def _ensure_customer_owns(db: Session, ticket: Ticket, current_user) -> None:
    if ticket.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": "只有工单所属客户可以评价",
            },
        )


def create_evaluation(
    db: Session,
    *,
    ticket_id: UUID,
    body: EvaluationCreate,
    current_user,
    ip_address: str | None = None,
) -> dict:
    """Create the single evaluation for a closed ticket as its owner.

    Only the owning customer may evaluate; staff get 403. Only ``closed``
    tickets are eligible (400 otherwise).
    """
    ticket = _get_ticket_or_404(db, ticket_id)
    if current_user.role != "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": "只有客户可以提交评价",
            },
        )
    _ensure_customer_owns(db, ticket, current_user)

    if ticket.status != "closed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_state_transition",
                "message": f"只有 closed 状态的工单可以评价，当前状态 {ticket.status}",
                "current_status": ticket.status,
            },
        )

    evaluation = Evaluation(
        ticket_id=ticket.id,
        customer_id=current_user.id,
        rating=body.rating,
        comment=body.comment.strip() if body.comment else None,
    )
    db.add(evaluation)
    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="evaluation.created",
        entity_type="evaluation",
        entity_id=evaluation.id,
        new_value={
            "ticket_id": str(ticket.id),
            "rating": evaluation.rating,
            "has_comment": bool(evaluation.comment),
        },
        ip_address=ip_address,
    )
    try:
        # Flush + commit together so the UNIQUE(ticket_id) violation is
        # caught here and converted into a clean 409.
        db.flush()
        db.commit()
    except IntegrityError:
        # UNIQUE(ticket_id): a concurrent (or earlier) evaluation won.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conflict", "message": "该工单已经评价过，不能重复评价"},
        ) from None
    except Exception:
        db.rollback()
        raise
    db.refresh(evaluation)
    return {
        "id": str(evaluation.id),
        "ticket_id": str(ticket.id),
        "rating": evaluation.rating,
        "comment": evaluation.comment,
        "created_at": evaluation.created_at.isoformat(),
    }


def get_evaluation(db: Session, *, ticket_id: UUID, current_user) -> dict:
    """Return the ticket's evaluation, or 404 when none exists yet.

    The ticket owner may view it; staff may view any. Other customers get 403
    through the ownership check.
    """
    ticket = _get_ticket_or_404(db, ticket_id)
    if current_user.role == "customer":
        if ticket.customer_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "无权访问该工单评价"},
            )

    evaluation = db.scalar(
        select(Evaluation).where(Evaluation.ticket_id == ticket.id)
    )
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "该工单尚无评价"},
        )
    return {
        "id": str(evaluation.id),
        "ticket_id": str(ticket.id),
        "customer_id": str(evaluation.customer_id),
        "rating": evaluation.rating,
        "comment": evaluation.comment,
        "created_at": evaluation.created_at.isoformat(),
    }
