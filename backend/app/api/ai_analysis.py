"""AI analysis endpoints (W3-A).

Thin HTTP layer over ``ai_analysis_service``: parse the request, delegate,
translate service results into responses. All permission and business rules
live in the service.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas.ai_analysis import AIAnalysisResponse
from ..services import ai_analysis_service

router = APIRouter(prefix="/tickets", tags=["ai-analysis"])


@router.get("/{ticket_id}/ai-analysis", response_model=AIAnalysisResponse)
def get_ai_analysis(
    ticket_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the ticket's AI analysis job and status.

    Permission model: customer only their own ticket (403 otherwise); agent /
    admin any ticket; a missing ticket or missing job is 404.
    """
    return ai_analysis_service.get_analysis(
        db, ticket_id=ticket_id, current_user=current_user
    )


@router.post("/{ticket_id}/ai-analysis/trigger", response_model=AIAnalysisResponse)
def trigger_ai_analysis(
    ticket_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-run the AI analysis for a ticket (agent/admin only).

    Idempotent: an unfinished job is returned unchanged; a fresh pending job
    is created only when none exists or the previous one failed.
    """
    return ai_analysis_service.trigger_analysis(
        db, ticket_id=ticket_id, current_user=current_user
    )
