"""Reply-suggestion endpoints (W4-A).

Thin HTTP layer over ``reply_suggestion_service``. Reuses the existing reply
review / send / list endpoints for the draft lifecycle — no second review
pipeline is created.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi import status as http_status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas.reply_suggestion import (
    ReplySuggestionJobCustomerOut,
    ReplySuggestionJobOut,
    ReplySuggestionRequestResponse,
)
from ..services import reply_suggestion_service

router = APIRouter(prefix="/tickets", tags=["reply-suggestions"])


@router.post(
    "/{ticket_id}/reply-suggestions",
    response_model=ReplySuggestionRequestResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def trigger_reply_suggestion(
    ticket_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger an AI reply suggestion for an ``in_review`` ticket (staff only).

    Returns immediately with a ``pending`` job; the RAG + ChatProvider work
    happens in the RQ worker. A second trigger for the same ticket returns
    409 (at most one suggestion per ticket in this milestone).
    """
    return reply_suggestion_service.trigger_reply_suggestion(
        db, ticket_id=ticket_id, current_user=current_user
    )


@router.get("/{ticket_id}/reply-suggestions")
def get_reply_suggestion(
    ticket_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the ticket's reply-suggestion job state.

    agent/admin: full state (retry_count, error_message, reply_id,
    source_refs). The ticket's own customer: safe status only. Other
    customers: 403. Missing ticket or missing job: 404.
    """
    payload = reply_suggestion_service.get_reply_suggestion(
        db, ticket_id=ticket_id, current_user=current_user
    )
    if current_user.role == "customer":
        return ReplySuggestionJobCustomerOut(**payload)
    return ReplySuggestionJobOut(**payload)
