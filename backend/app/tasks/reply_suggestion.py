"""RQ task: ``reply_suggestion`` (W4-A).

Runs inside the RQ worker process. It implements the RAG-to-draft pipeline:

  ticket.title + ticket.description
  → embed the query
  → retrieve top_k ready knowledge chunks (pgvector cosine)
  → build a size-limited RAG context with real sources
  → call ChatProvider for a structured suggestion
  → Pydantic-validate the output
  → create one ``ticket_replies`` draft (is_ai_suggestion=true, status=draft)
  → mark the job succeeded

The draft, the job state and the audit row commit in a single transaction, so
a failure can never leave a half-created AI reply. Retries are idempotent: a
re-entry first checks whether a draft was already created for this job and
returns early, so retrying can never produce duplicate drafts.

AI can only create a *draft* — it never reviews, sends or closes the ticket.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from ..chat_provider import ChatProvider, ChatProviderError
from ..embedding import EmbeddingProviderError
from ..models import AIProcessingJob, Ticket, TicketReply
from ..provider_factory import get_chat_provider, get_embedding_provider
from ..schemas.reply_suggestion import (
    ReplySuggestionOutput,
    SourceRef,
)
from ..services import audit as audit_service
from ..services.knowledge_service import build_knowledge_context

MAX_ERROR_MESSAGE_LENGTH = 2000
# RAG retrieval + context budget.
RAG_TOP_K = 5
RAG_CONTEXT_MAX_CHARS = 2000


def _fail_job(db: Session, job: AIProcessingJob, message: str) -> None:
    """Move the job to failed with a safe, short error message.

    Only touches the job row — the ticket and any existing human replies are
    untouched, so a failure can never damage workflow data.
    """
    job.status = "failed"
    job.error_message = (message or "")[:MAX_ERROR_MESSAGE_LENGTH]
    db.commit()


def _retrieve_context(
    db: Session, ticket: Ticket
) -> tuple[list[dict], list[SourceRef]]:
    """Embed the ticket and retrieve top_k ready chunks + source refs.

    Returns ``(results, source_refs)``. ``results`` feed the RAG context and
    the ``source_refs`` are persisted on the draft so the agent can see where
    the suggestion came from. Only ``ready`` knowledge items are searched.
    """
    embedding_provider = get_embedding_provider()
    query_text = f"{ticket.title}\n{ticket.description}"
    query_vector = embedding_provider.embed_texts([query_text])[0]

    from sqlalchemy import select

    from ..models import KnowledgeChunk, KnowledgeItem

    rows = db.execute(
        select(
            KnowledgeChunk.content,
            KnowledgeChunk.knowledge_item_id,
            KnowledgeChunk.chunk_index,
            KnowledgeChunk.page_number,
            KnowledgeItem.title,
            KnowledgeChunk.embedding.cosine_distance(query_vector).label("distance"),
        )
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.knowledge_item_id)
        .where(KnowledgeItem.status == "ready")
        .where(KnowledgeChunk.embedding.is_not(None))
        .order_by(KnowledgeChunk.embedding.cosine_distance(query_vector))
        .limit(RAG_TOP_K)
    ).all()

    results: list[dict] = []
    source_refs: list[SourceRef] = []
    for row in rows:
        results.append(
            {
                "title": row.title,
                "chunk_index": row.chunk_index,
                "content": row.content,
            }
        )
        source_refs.append(
            SourceRef(
                knowledge_item_id=str(row.knowledge_item_id),
                title=row.title,
                chunk_index=row.chunk_index,
                page_number=row.page_number,
            )
        )
    return results, source_refs


def _build_prompt(ticket: Ticket, context: str, has_sources: bool) -> str:
    base = (
        "你是企业客服团队的 AI 回复助手。根据工单内容和知识库资料，"
        "生成一条客服回复建议。\n"
        f"工单标题：{ticket.title}\n"
        f"工单描述：{ticket.description}\n"
    )
    if has_sources:
        base += f"知识库资料：\n{context}\n"
    else:
        base += "知识库中没有匹配的资料。\n"
    base += (
        "请只输出 JSON 对象：\n"
        '{"reply": "给客户的回复正文", "confidence": 0到1, '
        '"should_escalate": true或false, "reason": "判断依据"}。\n'
        "reply 不得声称\"根据知识库\"当且仅当没有知识库资料时。"
    )
    return base


def run_reply_suggestion(job_id: str) -> dict:
    """RQ job body: generate one AI reply suggestion draft for a ticket.

    The job is fetched fresh in a new session (the worker is a separate
    process). A retry re-entry finds the job already ``succeeded`` (or the
    draft already created) and returns early — never a duplicate draft.
    """
    from ..database import SessionLocal

    with SessionLocal() as db:
        job = db.get(AIProcessingJob, UUID(job_id))
        if job is None:
            raise ValueError(f"AI 任务不存在: {job_id}")
        if job.status == "succeeded":
            return {"status": job.status}

        # Every execution (initial or retry) counts as one attempt; first run
        # moves pending -> processing.
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

        # Idempotency: a previous (retried) execution may already have created
        # the draft before the process died. Reuse it instead of duplicating.
        existing_draft = (
            db.query(TicketReply)
            .filter_by(ticket_id=ticket.id, is_ai_suggestion=True)
            .first()
        )
        if existing_draft is not None:
            job.status = "succeeded"
            job.error_message = None
            job.result = {
                "reply_id": str(existing_draft.id),
                "source_refs": existing_draft.source_refs or [],
            }
            db.commit()
            return {"status": job.status}

        try:
            # 1. RAG retrieval.
            results, source_refs = _retrieve_context(db, ticket)
            has_sources = bool(results)
            context = build_knowledge_context(results, max_chars=RAG_CONTEXT_MAX_CHARS)

            # 2. ChatProvider structured output.
            provider = get_chat_provider()
            raw_output = provider.extract_structured(
                system_prompt=(
                    "你是客服回复建议助手。只输出 JSON，不要输出任何其他文字。"
                ),
                user_prompt=_build_prompt(ticket, context, has_sources),
                schema=ReplySuggestionOutput,
            )
            # ``extract_structured`` only returns Pydantic-validated data.
            suggestion = ReplySuggestionOutput.model_validate(raw_output)

            # 3. No sources => must escalate and must not claim a source.
            #    These are *permanent* policy violations: retrying cannot fix
            #    them, so mark the job failed right away (no RQ retry loop).
            if not has_sources and not suggestion.should_escalate:
                db.rollback()
                _fail_job(db, job, "没有知识库依据时必须 should_escalate=true")
                raise ChatProviderError("没有知识库依据时必须 should_escalate=true")
            if not has_sources and "根据知识库" in suggestion.reply:
                db.rollback()
                _fail_job(db, job, "没有知识库依据时不得声称根据知识库")
                raise ChatProviderError("没有知识库依据时不得声称根据知识库")

            # 4. Create the AI draft + mark job succeeded + audit in ONE tx.
            #    ``sender_id`` is NOT NULL; AI drafts need a placeholder
            #    actor (the assignee, or the first staff member) — AI never
            #    actually "sends" anything.
            actor_id = _draft_sender_id(db, ticket)
            draft = TicketReply(
                ticket_id=ticket.id,
                sender_id=actor_id,
                content=suggestion.reply,
                status="draft",
                is_ai_suggestion=True,
                is_sent=False,
                source_refs=[ref.model_dump() for ref in source_refs],
            )

            db.add(draft)
            db.flush()

            job.status = "succeeded"
            job.error_message = None
            job.result = {
                "reply_id": str(draft.id),
                "source_refs": [ref.model_dump() for ref in source_refs],
            }

            audit_service.create_audit_log(
                db,
                actor_id=actor_id,
                action="reply_suggestion.succeeded",
                entity_type="ticket",
                entity_id=ticket.id,
                new_value={
                    "job_id": str(job.id),
                    "reply_id": str(draft.id),
                    "source_count": len(source_refs),
                },
            )
            audit_service.create_audit_log(
                db,
                actor_id=actor_id,
                action="reply.created",
                entity_type="ticket",
                entity_id=ticket.id,
                new_value={
                    "reply_id": str(draft.id),
                    "content_length": len(draft.content),
                    "is_ai_suggestion": True,
                    "status": "draft",
                },
            )

            db.commit()
        except (ChatProviderError, EmbeddingProviderError, ValueError) as exc:
            # Provider / validation failure: record the error and let RQ retry.
            # The job stays ``processing``; only after RQ exhausts its retries
            # does the worker's failure handler move it to ``failed``. No draft
            # was created, so a retry cannot duplicate anything.
            db.rollback()
            job.error_message = (str(exc) or exc.__class__.__name__)[
                :MAX_ERROR_MESSAGE_LENGTH
            ]
            db.commit()
            raise

        db.refresh(job)
        return {"status": job.status}


def _draft_sender_id(db: Session, ticket: Ticket) -> UUID:
    """Pick a sender for the AI draft (ticket_replies.sender_id is NOT NULL).

    Prefer the ticket's assignee; otherwise the first active staff member.
    This is a display/ownership placeholder — AI never "sends" anything.
    """
    from ..models import User

    if ticket.assignee_id is not None:
        assignee = db.get(User, ticket.assignee_id)
        if assignee is not None:
            return assignee.id
    staff = (
        db.query(User)
        .filter(User.role.in_(("agent", "admin")), User.is_active.is_(True))
        .first()
    )
    if staff is not None:
        return staff.id
    raise ValueError("没有可用的客服账号作为 AI 草稿的发送者")
