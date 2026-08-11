"""Ticket evaluation endpoints (W2-B).

Only the ticket's owning customer may evaluate a ``closed`` ticket, exactly
once. Staff may view evaluations.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi import status as http_status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas.evaluation import (
    EvaluationCreate,
    EvaluationCreateResponse,
    EvaluationOut,
)
from ..services import evaluation_service

router = APIRouter(prefix="/tickets", tags=["evaluations"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/{ticket_id}/evaluation",
    response_model=EvaluationCreateResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def create_evaluation(
    ticket_id: UUID,
    body: EvaluationCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rate a closed ticket as its owner (once)."""
    return evaluation_service.create_evaluation(
        db,
        ticket_id=ticket_id,
        body=body,
        current_user=current_user,
        ip_address=_client_ip(request),
    )


@router.get("/{ticket_id}/evaluation", response_model=EvaluationOut)
def get_evaluation(
    ticket_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the ticket's evaluation (owner or staff)."""
    return evaluation_service.get_evaluation(
        db, ticket_id=ticket_id, current_user=current_user
    )
