"""Pydantic schemas for ticket evaluation (W2-B).

Rating is a 1-5 integer (matches the database ``ck_evaluations_rating_range``
CHECK constraint). Comment is optional with a sane length cap.
"""

from pydantic import BaseModel, Field

MAX_EVALUATION_COMMENT_LENGTH = 2_000


class EvaluationCreate(BaseModel):
    model_config = {"extra": "forbid"}

    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(
        default=None, max_length=MAX_EVALUATION_COMMENT_LENGTH
    )


class EvaluationOut(BaseModel):
    id: str
    ticket_id: str
    customer_id: str
    rating: int
    comment: str | None
    created_at: str


class EvaluationCreateResponse(BaseModel):
    id: str
    ticket_id: str
    rating: int
    comment: str | None
    created_at: str
