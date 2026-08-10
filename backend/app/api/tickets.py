"""Ticket endpoints (W2-A).

Routes stay thin: they parse the HTTP request, delegate to the service layer,
and translate service results into HTTP responses. All business rules live in
``backend/app/services/``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..deps import get_current_user
from ..database import get_db
from ..models import User
from ..schemas.ticket import (
    TicketCreate,
    TicketCreateResponse,
    TicketDetail,
    TicketUpdate,
    TicketUpdateResponse,
    TransitionRequest,
    TransitionResponse,
)
from ..services import ticket_service
from ..services.idempotency import IdempotencyConflict, IdempotencyReplay

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP for the audit trail (may be None behind proxies)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "",
    response_model=TicketCreateResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def create_ticket(
    body: TicketCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a ticket as the current customer.

    ``Idempotency-Key`` is optional. Repeating the same key with the same
    body returns the original ticket (200) without creating a duplicate.
    """
    if idempotency_key is not None and (
        not idempotency_key.strip() or len(idempotency_key) > ticket_service.IDEMPOTENCY_KEY_LIMIT
    ):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_input",
                "message": f"Idempotency-Key 长度需在 1-{ticket_service.IDEMPOTENCY_KEY_LIMIT} 之间",
            },
        )

    try:
        payload, http_code = ticket_service.create_ticket(
            db,
            body=body,
            current_user=current_user,
            idempotency_key=idempotency_key,
            ip_address=_client_ip(request),
        )
    except IdempotencyReplay as replay:
        return JSONResponse(status_code=200, content=replay.response)
    except IdempotencyConflict as conflict:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=conflict.detail) from conflict
    except SQLAlchemyError:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "internal_error", "message": "创建工单失败，请稍后重试"},
        )
    return payload


@router.get("")
def list_tickets(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    priority: str | None = None,
    classification: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List tickets with role-scoped visibility and SQL pagination/filters.

    - customer: only their own tickets;
    - agent / admin: every ticket.
    Invalid page/page_size values return 400; empty results return ``items: []``
    rather than 404.
    """
    if page < 1:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "page 必须大于等于 1"},
        )
    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "page_size 必须在 1-100 之间"},
        )

    result = ticket_service.list_tickets(
        db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        status_filter=status,
        priority_filter=priority,
        classification_filter=classification,
    )
    return result


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket_detail(
    ticket_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a ticket with its replies and audit summary.

    Replies and audit entries are ordered oldest-first. Customers may only
    read their own tickets (403 otherwise); a missing ticket is 404.
    """
    return ticket_service.get_ticket_detail(
        db, current_user=current_user, ticket_id=ticket_id
    )


@router.patch("/{ticket_id}", response_model=TicketUpdateResponse)
def update_ticket(
    body: TicketUpdate,
    ticket_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update allowed fields on a ticket.

    Permission split: customers may only change title/description on their
    own ``open`` ticket; agent/admin may change priority/classification.
    ``status`` is never accepted here — use the transition endpoint.
    """
    return ticket_service.update_ticket(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        body=body,
        ip_address=_client_ip(request),
    )


@router.post("/{ticket_id}/transition", response_model=TransitionResponse)
def transition_ticket(
    body: TransitionRequest,
    ticket_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fire a state-machine event, e.g. ``{"event": "start_review"}``.

    Illegal transitions return 400 with ``current_status`` and
    ``allowed_events``; role violations return 403; missing tickets return
    404. The transition and its audit log commit in one transaction.
    """
    return ticket_service.transition_ticket(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        event=body.event,
        ip_address=_client_ip(request),
    )
