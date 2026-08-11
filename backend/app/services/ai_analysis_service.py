"""AI analysis business logic — request path (W3-A).

The web process is only responsible for *scheduling*: record a ``pending``
``ai_processing_jobs`` row and hand the AI call to RQ so the HTTP request
never waits on a model. The actual execution lives in
``backend/app/tasks/ticket_analysis.py``; keeping the two paths separate means
the web process does not import network/LLM code and the worker does not run
FastAPI request handling.

Duplicate jobs are prevented at three layers:
- the stable RQ ``job_id`` ``ticket_analysis-{ticket_id}``;
- the ``UNIQUE(ticket_id, job_type)`` database constraint;
- a re-enqueue guard that refuses to queue a second unfinished job.

Task-state is stored only in ``ai_processing_jobs`` — never in a Python
in-memory dictionary.
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
from ..models import AIProcessingJob, Ticket

# Stable RQ job id so the same ticket can never be queued twice. RQ only
# allows letters/digits/underscore/dash in job ids, so we use ``-`` as the
# separator (the docs sketch ``ticket_analysis:{ticket_id}`` but the colon is
# not a legal RQ job-id character; the id is still deterministic per ticket).
AI_JOB_PREFIX = "ticket_analysis-"
# Fully qualified path to the RQ job body; workers import it by name.
TICKET_ANALYSIS_TASK_PATH = "backend.app.tasks.ticket_analysis.run_ticket_analysis"


def _get_ticket_or_404(db: Session, ticket_id: UUID) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id))
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "工单不存在"},
        )
    return ticket


def _ensure_can_view(current_user, ticket: Ticket) -> None:
    """A customer may only see their own tickets; staff see everything."""
    if current_user.role == "customer" and ticket.customer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "无权访问该工单"},
        )


def _redis_client() -> Redis:
    return Redis.from_url(get_redis_url())


def _enqueue_ticket_analysis(job: AIProcessingJob) -> None:
    """Enqueue ``ticket_analysis`` with a stable job_id and RQ retry policy.

    The ``job_id`` is ``ticket_analysis-{ticket_id}`` so Redis itself refuses
    to enqueue the same ticket twice. If a job with that id already exists in
    a non-terminal state, RQ returns the existing job and nothing new is
    enqueued. A raise inside RQ (e.g. Redis unreachable) is swallowed here:
    the request must not fail because of the queue — the database row already
    records the pending job.
    """
    redis = _redis_client()
    queue = Queue("default", connection=redis)
    job_id = f"{AI_JOB_PREFIX}{job.ticket_id}"
    try:
        existing = queue.fetch_job(job_id)
        if existing is not None and existing.get_status() in ("queued", "started", "deferred", "scheduled"):
            return
        queue.enqueue(
            TICKET_ANALYSIS_TASK_PATH,
            str(job.id),
            job_id=job_id,
            retry=Retry(max=RQ_MAX_RETRIES, interval=RQ_RETRY_DELAYS),
        )
    except Exception:
        # The database row is the source of truth for the pending job; losing
        # the enqueue must not break the ticket creation response.
        pass


def create_analysis_job(
    db: Session,
    *,
    ticket_id: UUID,
    enqueue: bool = True,
) -> AIProcessingJob | None:
    """Create a ``pending`` analysis job and enqueue it (called after create).

    Guard: if a job for the same (ticket, job_type) already exists we do
    nothing — one ticket never gets two analysis rows. A finished job is not
    recreated here either; re-analysis goes through ``trigger_analysis``. The
    ``UNIQUE(ticket_id, job_type)`` constraint is the concurrency backstop:
    on a lost race the insert fails and we keep the winner's row.
    """
    existing = db.scalar(
        select(AIProcessingJob).where(
            AIProcessingJob.ticket_id == ticket_id,
            AIProcessingJob.job_type == "ticket_analysis",
        )
    )
    if existing is not None:
        return None

    job = AIProcessingJob(
        ticket_id=ticket_id,
        job_type="ticket_analysis",
        business_key=f"{AI_JOB_PREFIX}{ticket_id}",
        status="pending",
        attempts=0,
        max_attempts=RQ_MAX_RETRIES + 1,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent create already inserted the analysis row. Roll back
        # this stale session state and return None (nothing to do).
        db.rollback()
        return None
    if enqueue:
        _enqueue_ticket_analysis(job)
    db.commit()
    db.refresh(job)
    return job


def trigger_analysis(
    db: Session,
    *,
    ticket_id: UUID,
    current_user,
) -> dict[str, Any]:
    """Re-run the analysis for a ticket, or create it when missing.

    ``POST /tickets/{id}/ai-analysis/trigger`` (staff only). Idempotency:
    - a pending/processing job is returned unchanged;
    - a succeeded job is returned unchanged (content changes are not
      auto-re-analysed; re-running is a deliberate staff action);
    - a failed job is reset to ``pending`` and re-enqueued on the same row —
      the ``UNIQUE(ticket_id, job_type)`` constraint guarantees there is never
      more than one analysis job per ticket.
    """
    ticket = _get_ticket_or_404(db, ticket_id)
    _ensure_can_view(current_user, ticket)
    if current_user.role not in ("agent", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": "只有客服或管理员可以重新触发 AI 分析",
            },
        )

    job = db.scalar(
        select(AIProcessingJob).where(
            AIProcessingJob.ticket_id == ticket.id,
            AIProcessingJob.job_type == "ticket_analysis",
        )
    )
    if job is None:
        # Never analysed: create the first pending job and enqueue it.
        job = create_analysis_job(db, ticket_id=ticket.id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "internal_error", "message": "AI 任务创建失败"},
            )
        return _serialize_job(ticket.id, job)

    if job.status in ("pending", "processing", "succeeded"):
        # Already running or finished — do not start a second run.
        return _serialize_job(ticket.id, job)

    # Failed: reset the same row and re-enqueue with the same stable job_id
    # (RQ allows re-enqueueing a terminal job id). RQ's retry budget starts
    # fresh per RQ job; the DB attempts counter keeps the full history.
    job.status = "pending"
    job.error_message = None
    job.last_error_at = None
    db.flush()
    _enqueue_ticket_analysis(job)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(job)
    return _serialize_job(ticket.id, job)


def get_analysis(
    db: Session,
    *,
    ticket_id: UUID,
    current_user,
) -> dict[str, Any]:
    """Return the latest analysis job for a ticket (``GET``).

    Permission model: customer only their own ticket (403 otherwise); agent /
    admin any ticket; missing ticket 404; missing job 404.
    """
    ticket = _get_ticket_or_404(db, ticket_id)
    _ensure_can_view(current_user, ticket)

    job = db.scalar(
        select(AIProcessingJob)
        .where(
            AIProcessingJob.ticket_id == ticket.id,
            AIProcessingJob.job_type == "ticket_analysis",
        )
        .order_by(AIProcessingJob.created_at.desc())
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "该工单尚无 AI 分析任务"},
        )
    return _serialize_job(ticket.id, job)


def _serialize_job(ticket_id: UUID, job: AIProcessingJob) -> dict[str, Any]:
    return {
        "ticket_id": str(ticket_id),
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "retry_count": job.attempts,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "result": job.result,
    }
