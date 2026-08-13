"""Reply-suggestion business logic — request path (W4-A).

The web process only *schedules*: it validates the ticket state, creates a
``pending`` ``ai_processing_jobs`` row (``job_type='reply_suggestion'``) and
enqueues the RQ task, so ``POST /tickets/{id}/reply-suggestions`` returns
immediately. The heavy work — RAG retrieval, ChatProvider call, draft
creation — happens in the worker (``tasks/reply_suggestion.py``).

Idempotency rests on the existing ``UNIQUE(ticket_id, job_type)`` constraint
plus the stable RQ job_id ``reply_suggestion-{ticket_id}``: one ticket can
have at most one ``reply_suggestion`` job, and a second trigger returns 409
when a job already exists (pending / processing / succeeded / failed).
"""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from redis import Redis
from rq import Queue, Retry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import (
    RQ_MAX_RETRIES,
    RQ_RETRY_DELAYS,
    get_redis_url,
)
from ..models import AIProcessingJob, Ticket, TicketReply

# Stable RQ job id (dash separator: RQ rejects colons in job ids).
REPLY_SUGGESTION_JOB_PREFIX = "reply_suggestion-"
# Fully qualified path to the RQ job body; workers import it by name.
REPLY_SUGGESTION_TASK_PATH = (
    "backend.app.tasks.reply_suggestion.run_reply_suggestion"
)

# Only ``in_review`` tickets may ask for a suggestion.
REPLY_SUGGESTION_ALLOWED_STATUSES = ("in_review",)


def reply_suggestion_failed_callback(job, connection, *args, **kwargs) -> None:
    """RQ ``on_failure`` callback: mark the DB job failed on final retry.

    Called only after RQ has exhausted the retry budget. It marks the
    ``ai_processing_jobs`` row ``failed`` (the durable state machine), so the
    API can show a terminal state. Never touches the ticket or any reply.
    """
    try:
        from ..database import SessionLocal

        with SessionLocal() as db:
            row = db.get(AIProcessingJob, job.id)
            if row is not None and row.status != "succeeded":
                row.status = "failed"
                db.commit()
    except Exception:
        # A failed callback must never crash the worker; the job row keeps the
        # error_message already recorded by the task body.
        pass


class ReplySuggestionConflict(Exception):
    """A reply_suggestion job already exists for this ticket."""

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__("reply suggestion already exists")
        self.detail = detail


def _get_ticket_or_404(db: Session, ticket_id: UUID) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id))
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "工单不存在"},
        )
    return ticket


def _require_staff(current_user) -> None:
    if current_user.role not in ("agent", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "只有客服或管理员可以触发 AI 回复建议"},
        )


def _ensure_customer_owns(current_user, ticket: Ticket) -> None:
    if current_user.role == "customer" and ticket.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "无权访问该工单"},
        )


def _redis_client() -> Redis:
    return Redis.from_url(get_redis_url())


def _enqueue_reply_suggestion(job: AIProcessingJob) -> None:
    """Enqueue ``reply_suggestion`` with a stable job_id and RQ retry policy.

    If a job with the same id is still queued/scheduled, RQ returns the
    existing one and nothing new is enqueued. Queue errors are swallowed: the
    ``ai_processing_jobs.status`` column is the durable source of truth and a
    later trigger can retry.
    """
    redis = _redis_client()
    queue = Queue("default", connection=redis)
    job_id = f"{REPLY_SUGGESTION_JOB_PREFIX}{job.ticket_id}"
    try:
        existing = queue.fetch_job(job_id)
        if existing is not None and existing.get_status() in (
            "queued",
            "started",
            "deferred",
            "scheduled",
        ):
            return
        queue.enqueue(
            REPLY_SUGGESTION_TASK_PATH,
            str(job.id),
            job_id=job_id,
            retry=Retry(max=RQ_MAX_RETRIES, interval=RQ_RETRY_DELAYS),
            on_failure=(
                "backend.app.services.reply_suggestion_service."
                "reply_suggestion_failed_callback"
            ),
        )
    except Exception:
        # The database row is the source of truth; losing the enqueue must not
        # break the trigger response.
        pass


def _find_suggestion_job(db: Session, ticket_id: UUID) -> AIProcessingJob | None:
    return db.scalar(
        select(AIProcessingJob).where(
            AIProcessingJob.ticket_id == ticket_id,
            AIProcessingJob.job_type == "reply_suggestion",
        )
    )


def trigger_reply_suggestion(
    db: Session,
    *,
    ticket_id: UUID,
    current_user,
) -> dict[str, Any]:
    """Create a ``pending`` reply-suggestion job and enqueue it.

    Only agent/admin may trigger; only ``in_review`` tickets are eligible
    (otherwise 400). If a job already exists for this ticket (any status),
    return 409 — MVP allows at most one suggestion per ticket.
    """
    _require_staff(current_user)
    ticket = _get_ticket_or_404(db, ticket_id)

    if ticket.status not in REPLY_SUGGESTION_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_state_transition",
                "message": f"只有 in_review 状态的工单可以生成 AI 回复建议，当前状态 {ticket.status}",
                "current_status": ticket.status,
            },
        )

    existing = _find_suggestion_job(db, ticket.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "conflict",
                "message": "该工单已有 AI 回复建议任务，不能重复触发",
                "job_id": str(existing.id),
                "status": existing.status,
            },
        )

    job = AIProcessingJob(
        ticket_id=ticket.id,
        job_type="reply_suggestion",
        business_key=f"{REPLY_SUGGESTION_JOB_PREFIX}{ticket.id}",
        status="pending",
        attempts=0,
        max_attempts=RQ_MAX_RETRIES + 1,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent trigger won the unique-constraint race.
        db.rollback()
        existing = _find_suggestion_job(db, ticket.id)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conflict",
                    "message": "该工单已有 AI 回复建议任务，不能重复触发",
                    "job_id": str(existing.id),
                    "status": existing.status,
                },
            ) from None
        raise

    _enqueue_reply_suggestion(job)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(job)

    return {
        "ticket_id": str(ticket.id),
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
    }


def get_reply_suggestion(
    db: Session,
    *,
    ticket_id: UUID,
    current_user,
) -> dict[str, Any]:
    """Return the ticket's reply-suggestion job state.

    agent/admin: full state incl. retry_count, error_message, reply_id and
    source_refs. The ticket's own customer: only the safe status fields
    (never draft content, sources, errors or model config). Other customers
    get 403; a missing ticket is 404; a ticket without a job is 404.
    """
    ticket = _get_ticket_or_404(db, ticket_id)
    _ensure_customer_owns(current_user, ticket)

    job = _find_suggestion_job(db, ticket.id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "该工单尚无 AI 回复建议任务"},
        )

    if current_user.role == "customer":
        return {
            "ticket_id": str(ticket.id),
            "job_id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        }

    reply_id = None
    if job.result:
        reply_id = job.result.get("reply_id")
    source_refs = (job.result or {}).get("source_refs", []) if job.result else []

    return {
        "ticket_id": str(ticket.id),
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "retry_count": job.attempts,
        "error_message": job.error_message,
        "reply_id": reply_id,
        "source_refs": source_refs,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }
