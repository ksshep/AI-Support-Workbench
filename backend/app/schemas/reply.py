"""Pydantic schemas for reply creation, review, send and listing.

``extra="forbid"`` keeps the body from smuggling ownership or lifecycle fields
(``sender_id``, ``status``, ``reviewer_id``, ``sent_at``, ``is_ai_suggestion``)
that must always come from the current user or from the service layer.
"""

from pydantic import BaseModel, Field, field_validator

MAX_REPLY_CONTENT_LENGTH = 10_000


class ReplyCreate(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1, max_length=MAX_REPLY_CONTENT_LENGTH)

    @field_validator("content")
    @classmethod
    def strip_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class ReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}

    approved: bool


class ReplyCreateResponse(BaseModel):
    id: str
    ticket_id: str
    content: str
    status: str
    sender_id: str
    sender_name: str
    is_ai_suggestion: bool
    created_at: str


class ReviewResponse(BaseModel):
    id: str
    ticket_id: str
    status: str
    approved: bool
    reviewer_id: str | None
    reviewed_at: str | None


class SendResponse(BaseModel):
    reply_id: str
    ticket_id: str
    reply_status: str
    ticket_status: str
    sent_at: str
