"""Pydantic schemas for AI ticket analysis (W3-A).

``TicketAnalysis`` is the *output contract*: every AI-provided value passes
through it before it is allowed anywhere near the database. The enums mirror
the ``tickets`` CHECK constraints from ``models.py``; ``confidence`` is a
probability in [0, 1]; the length caps match the column sizes. JSON that is
malformed, missing a field or carrying an out-of-range value fails here, and
the worker records the failure instead of writing unvalidated data.

``AIAnalysisResponse`` is the payload of ``GET /tickets/{id}/ai-analysis``.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ``category`` is a finite enum so classification stays consistent across
# tickets and can be used for filtering and reporting.
VALID_CATEGORIES = (
    "billing",
    "account",
    "technical",
    "product",
    "other",
)

MAX_SUMMARY_LENGTH = 500
MAX_REASON_LENGTH = 500


class TicketAnalysis(BaseModel):
    """Structured output of the AI ticket analysis.

    Every field is mandatory: a missing field fails validation (and therefore
    the job), so a partial model answer can never be persisted.
    """

    category: Literal["billing", "account", "technical", "product", "other"]
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    priority: Literal["low", "normal", "high", "urgent"]
    sentiment: Literal["positive", "neutral", "negative"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)


class AIAnalysisResponse(BaseModel):
    """Response of ``GET /tickets/{ticket_id}/ai-analysis``."""

    ticket_id: str
    job_id: str
    job_type: str
    status: str
    retry_count: int
    error_message: str | None
    created_at: str
    updated_at: str
    result: TicketAnalysis | None = None
