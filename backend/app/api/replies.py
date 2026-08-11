"""Reply endpoints (W2-B).

Thin HTTP layer: parse request, delegate to ``reply_service``, translate
service responses. All lifecycle rules live in the service.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi import status as http_status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas.reply import (
    ReplyCreate,
    ReplyCreateResponse,
    ReviewRequest,
    ReviewResponse,
    SendResponse,
)
from ..services import reply_service

router = APIRouter(prefix="/tickets", tags=["replies"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/{ticket_id}/replies",
    response_model=ReplyCreateResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def create_reply(
    ticket_id: UUID,
    body: ReplyCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a ``draft`` reply as an agent/admin."""
    return reply_service.create_reply(
        db,
        ticket_id=ticket_id,
        body=body,
        current_user=current_user,
        ip_address=_client_ip(request),
    )


@router.get("/{ticket_id}/replies")
def list_replies(
    ticket_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List a ticket's replies.

    Customers see only ``sent`` replies on their own ticket; staff see all.
    """
    return reply_service.list_replies(
        db, ticket_id=ticket_id, current_user=current_user
    )


@router.post("/{ticket_id}/replies/{reply_id}/review", response_model=ReviewResponse)
def review_reply(
    ticket_id: UUID,
    reply_id: UUID,
    body: ReviewRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Approve (draft -> reviewed) or reject (stays draft) a reply."""
    return reply_service.review_reply(
        db,
        ticket_id=ticket_id,
        reply_id=reply_id,
        approved=body.approved,
        current_user=current_user,
        ip_address=_client_ip(request),
    )


@router.post("/{ticket_id}/replies/{reply_id}/send", response_model=SendResponse)
def send_reply(
    ticket_id: UUID,
    reply_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a reviewed reply; ticket moves in_review -> replied."""
    return reply_service.send_reply(
        db,
        ticket_id=ticket_id,
        reply_id=reply_id,
        current_user=current_user,
        ip_address=_client_ip(request),
    )
