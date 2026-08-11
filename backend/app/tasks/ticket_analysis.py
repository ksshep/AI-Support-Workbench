"""RQ task: ``ticket_analysis`` (W3-A).

This module runs inside the RQ worker process. It performs the actual AI call
and persists the validated result. Splitting it from the service keeps the web
process free of network/LLM code and lets the worker import only what it
needs.

Status flow: ``pending -> processing -> succeeded``, or ``failed`` after the
provider/output validation fails and the RQ retry budget is exhausted. State
is persisted in ``ai_processing_jobs`` only. The ChatProvider abstraction
means this code never names a vendor, a URL, an API key or a model id.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from ..chat_provider import ChatProviderError
from ..models import AIProcessingJob, Ticket
from ..provider_factory import get_chat_provider
from ..schemas.ai_analysis import TicketAnalysis
from ..services import audit as audit_service

# Mapping from the AI output schema to the ticket columns. The model calls the
# classification ``category`` (the task spec's output field); the ticket
# column is ``classification``. ``status`` and ``customer_id`` are
# deliberately absent: AI must never change workflow state or ownership.
AI_RESULT_TO_TICKET_FIELDS = {
    "category": "classification",
    "summary": "summary",
    "priority": "priority",
    "sentiment": "sentiment",
}

# Maximum length of the failure message stored on the job row.
MAX_ERROR_MESSAGE_LENGTH = 2000


def _fail_job(db: Session, job: AIProcessingJob, message: str) -> None:
    """Move a job to failed, recording the error.

    Only ever touches the job row — the ticket keeps whatever data it had, so
    a failed AI call can never destroy or downgrade existing ticket content.
    """
    job.status = "failed"
    job.error_message = (message or "")[:MAX_ERROR_MESSAGE_LENGTH]
    db.commit()


def run_ticket_analysis(job_id: str) -> dict:
    """RQ job body: analyse one ticket and persist the validated result.

    The job is fetched fresh in a new session (the worker is a separate
    process from the web request that created it). Every state change commits
    in one transaction; the database is the only source of truth for job
    state. Provider failures re-raise so RQ applies its retry policy, and the
    ``attempts`` counter keeps the DB record in sync with RQ's retry count.
    """
    from ..database import SessionLocal

    with SessionLocal() as db:
        job = db.get(AIProcessingJob, UUID(job_id))
        if job is None:
            raise ValueError(f"AI 任务不存在: {job_id}")
        if job.status == "succeeded":
            return {"status": job.status}

        # Every execution (initial or retry) counts as one attempt. The first
        # run also moves the job pending -> processing; a retry re-enters with
        # status already ``processing`` and skips straight to the work.
        job.attempts += 1
        if job.status != "processing":
            job.status = "processing"
        db.commit()
        db.refresh(job)

        ticket = db.get(Ticket, job.ticket_id)
        if ticket is None:
            db.rollback()
            _fail_job(db, job, "工单不存在")
            raise ValueError("ticket missing")

        provider = get_chat_provider()
        try:
            result = provider.extract_structured(
                system_prompt=(
                    "你是客服工单分析助手。只输出 JSON，不要输出任何其他文字。"
                ),
                user_prompt=_build_analysis_prompt(ticket.title, ticket.description),
                schema=TicketAnalysis,
            )
        except ChatProviderError as exc:
            # Provider / output-validation failure: record it and let RQ retry.
            job.error_message = (str(exc) or exc.__class__.__name__)[
                :MAX_ERROR_MESSAGE_LENGTH
            ]
            db.commit()
            db.refresh(job)
            raise

        # ``extract_structured`` only returns data that already passed Pydantic
        # validation; no unvalidated model output can reach this point.
        job.result = result
        job.status = "succeeded"
        job.error_message = None
        for ai_field, ticket_field in AI_RESULT_TO_TICKET_FIELDS.items():
            setattr(ticket, ticket_field, result[ai_field])

        audit_service.create_audit_log(
            db,
            actor_id=None,
            action="ticket.ai_analyzed",
            entity_type="ticket",
            entity_id=ticket.id,
            new_value={
                "job_type": job.job_type,
                "result": result,
            },
        )
        try:
            db.commit()
        except Exception:
            db.rollback()
            _fail_job(db, job, "写入 AI 分析结果失败")
            raise
        db.refresh(job)
        return {"status": job.status}


def _build_analysis_prompt(title: str, description: str) -> str:
    return (
        "请分析以下客服工单，输出 JSON 对象：\n"
        f"标题：{title}\n"
        f"描述：{description}\n"
        "category 只能是 billing/account/technical/product/other 之一；\n"
        "priority 只能是 low/normal/high/urgent；\n"
        "sentiment 只能是 positive/neutral/negative；\n"
        "confidence 是 0 到 1 之间的数字；\n"
        "summary 一句话摘要；reason 说明判断依据。"
    )
