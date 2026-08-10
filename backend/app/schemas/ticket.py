"""Pydantic schemas for tickets.

Validation limits mirror the database column sizes from ``models.py``:
title is ``String(200)`` and description is unbounded ``Text``. ``priority``
and ``classification`` may be updated by staff and are validated here before
reaching the database CHECK constraints.
"""

from pydantic import BaseModel, Field, field_validator

from ..models import TICKET_PRIORITIES

# description is TEXT in the database; keep an application-level cap so a
# single request cannot carry an arbitrarily large payload.
MAX_DESCRIPTION_LENGTH = 10_000
MAX_CLASSIFICATION_LENGTH = 50


class TicketCreate(BaseModel):
    """``extra="forbid"`` keeps the body from smuggling fields the API never
    accepts (e.g. ``customer_id`` or ``status``) — the client must not be
    able to influence who owns a ticket or what state it is in.
    """

    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    classification: str | None = Field(
        default=None, max_length=MAX_CLASSIFICATION_LENGTH
    )

    @field_validator("title", "description", "classification")
    @classmethod
    def strip_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class TicketUpdate(BaseModel):
    """Fields the PATCH endpoint accepts.

    ``customer_id`` is intentionally not present: it can never be changed and
    is never read from the request body. ``status`` is intentionally absent so
    the state machine remains the only way to move a ticket. Unknown fields
    are rejected (``extra="forbid"``).
    """

    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(
        default=None, min_length=1, max_length=MAX_DESCRIPTION_LENGTH
    )
    priority: str | None = Field(default=None)
    classification: str | None = Field(
        default=None, max_length=MAX_CLASSIFICATION_LENGTH
    )

    @field_validator("title", "description", "classification")
    @classmethod
    def strip_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str | None) -> str | None:
        if value is not None and value not in TICKET_PRIORITIES:
            raise ValueError(
                f"priority must be one of {', '.join(TICKET_PRIORITIES)}"
            )
        return value


class TransitionRequest(BaseModel):
    event: str = Field(min_length=1, max_length=50)


class TicketBrief(BaseModel):
    """List-item representation; no sensitive fields."""

    id: str
    title: str
    status: str
    priority: str
    classification: str
    summary: str
    sentiment: str
    customer_name: str
    assignee_name: str | None
    reply_count: int
    created_at: str
    updated_at: str


class AuditSummary(BaseModel):
    action: str
    old_value: dict | None
    new_value: dict | None
    created_at: str


class ReplyBrief(BaseModel):
    id: str
    content: str
    is_ai_suggestion: bool
    is_sent: bool
    sender_name: str
    created_at: str


class TicketDetail(BaseModel):
    """Full ticket payload returned by ``GET /tickets/{id}``.

    Replies and audit summaries are returned in chronological order.
    """

    id: str
    title: str
    description: str
    status: str
    priority: str
    classification: str
    summary: str
    sentiment: str
    customer_id: str
    customer_name: str
    assignee_id: str | None
    assignee_name: str | None
    replies: list[ReplyBrief]
    audit: list[AuditSummary]
    created_at: str
    updated_at: str


class TicketCreateResponse(BaseModel):
    id: str
    title: str
    status: str
    priority: str
    classification: str
    summary: str
    sentiment: str
    created_at: str


class TicketUpdateResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    priority: str
    classification: str
    created_at: str
    updated_at: str


class TransitionResponse(BaseModel):
    id: str
    status: str
    allowed_events: list[str]
    updated_at: str
