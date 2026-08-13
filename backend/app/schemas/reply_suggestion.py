"""Pydantic schemas for RAG-powered reply suggestions (W4-A).

``ReplySuggestionOutput`` is the structured-output contract for the Chat
Provider: every model answer passes through it (and its Pydantic validation)
before it can become a reply draft. ``reply`` is the only field that ends up
in ``ticket_replies.content`` — never the model's raw free text.

Response schemas are defensive: staff see job state + a source summary;
customers only see a safe task status (never draft content, sources, error
details or model configuration).
"""

from typing import Literal

from pydantic import BaseModel, Field

# Reply content cap matches the ``ticket_replies.content`` CHECK constraint.
MAX_REPLY_CONTENT_LENGTH = 10_000
MAX_REASON_LENGTH = 500
MAX_SOURCE_REFS = 10


class SourceRef(BaseModel):
    """Minimal, non-forgeable source citation for a generated reply."""

    knowledge_item_id: str
    title: str
    chunk_index: int
    page_number: int | None = None


class ReplySuggestionOutput(BaseModel):
    """Structured output of the reply-suggestion Chat Provider call.

    Every field is mandatory: missing/blank/out-of-range values fail
    validation, and therefore the whole job, so unvalidated model output can
    never become a draft.
    """

    reply: str = Field(min_length=1, max_length=MAX_REPLY_CONTENT_LENGTH)
    confidence: float = Field(ge=0.0, le=1.0)
    should_escalate: bool
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)


class ReplySuggestionRequestResponse(BaseModel):
    """``POST /tickets/{id}/reply-suggestions`` immediate response (201)."""

    ticket_id: str
    job_id: str
    job_type: str
    status: str


class ReplySuggestionJobOut(BaseModel):
    """Full job state for agent/admin (``GET``)."""

    ticket_id: str
    job_id: str
    job_type: str
    status: str
    retry_count: int
    error_message: str | None = None
    reply_id: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ReplySuggestionJobCustomerOut(BaseModel):
    """Safe job state for the ticket's own customer.

    Carries only the status — no draft content, no sources, no error details.
    """

    ticket_id: str
    job_id: str
    job_type: str
    status: str
    created_at: str
    updated_at: str
