"""Reply-suggestion RQ task tests (W4-A).

The task body ``run_reply_suggestion`` is run directly with the Fake Chat
Provider and Fake Embedding Provider so the whole RAG-to-draft pipeline is
exercised against the real test database offline: pending -> processing ->
succeeded, the AI draft being created with the right flags, source refs
persisted from real retrieval, and every failure path leaving the job failed
without a half-created draft.
"""

import pytest

from backend.app.chat_provider import (
    FakeChatProvider,
    ProviderTimeoutError,
    StructuredOutputError,
)
from backend.app.embedding import FakeEmbeddingProvider
from backend.app.models import (
    AIProcessingJob,
    AuditLog,
    KnowledgeItem,
    Ticket,
    TicketReply,
)
from tests.fixtures import sample_txt_bytes


def _user_id(db, user_fixture):
    """Return the DB user id for a role fixture (agent/admin/customer)."""
    from backend.app.models import User

    return db.query(User).filter_by(email=user_fixture["email"]).one().id


def _ready_knowledge(db, customer, content=None):
    """Create a ready knowledge item (uploaded + ingested) for RAG."""
    from backend.app.models import User
    from backend.app.tasks.knowledge_ingestion import run_knowledge_ingestion

    uploader = db.query(User).filter_by(email=customer["email"]).one()
    item = KnowledgeItem(
        title="账号登录 FAQ",
        content="",
        source_type="txt",
        file_name="faq.txt",
        file_size_bytes=max(1, len(content or sample_txt_bytes())),
        file_content=content or sample_txt_bytes(),
        status="processing",
        uploaded_by=uploader.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    # Ingest with fake embedding.
    from backend.app import provider_factory
    import backend.app.tasks.knowledge_ingestion as task_mod

    provider = FakeEmbeddingProvider()
    original = task_mod.get_embedding_provider
    task_mod.get_embedding_provider = lambda: provider
    provider_factory._embedding_provider_cache = provider
    try:
        run_knowledge_ingestion(str(item.id))
    finally:
        task_mod.get_embedding_provider = original
    db.expire_all()
    return db.get(KnowledgeItem, item.id)


def _make_job(db, ticket_id):
    """Create a pending reply_suggestion job (no Redis)."""
    from backend.app.services.reply_suggestion_service import (
        REPLY_SUGGESTION_JOB_PREFIX,
    )

    job = AIProcessingJob(
        ticket_id=ticket_id,
        job_type="reply_suggestion",
        business_key=f"{REPLY_SUGGESTION_JOB_PREFIX}{ticket_id}",
        status="pending",
        attempts=0,
        max_attempts=3,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _run(job_id, chat_provider=None):
    """Run the task body, swapping in providers."""
    import backend.app.tasks.reply_suggestion as task_mod
    from backend.app import provider_factory

    chat_provider = chat_provider or FakeChatProvider()
    original_chat = task_mod.get_chat_provider
    original_emb = task_mod.get_embedding_provider
    task_mod.get_chat_provider = lambda: chat_provider
    task_mod.get_embedding_provider = lambda: FakeEmbeddingProvider()
    provider_factory._chat_provider_cache = chat_provider
    provider_factory._embedding_provider_cache = FakeEmbeddingProvider()
    try:
        return task_mod.run_reply_suggestion(str(job_id))
    except Exception as exc:
        return exc
    finally:
        task_mod.get_chat_provider = original_chat
        task_mod.get_embedding_provider = original_emb


def _ticket_in_review(db, ticket_id, agent_id):
    """Move a ticket to in_review (fire the state machine directly)."""
    from backend.app.services.state_machine import next_status

    ticket = db.get(Ticket, ticket_id)
    ticket.status = next_status(ticket.status, "start_review")
    db.commit()
    db.refresh(ticket)
    return ticket


# --------------------------------------------------------------------------
# Success: RAG hit -> draft
# --------------------------------------------------------------------------

def test_suggestion_with_knowledge_creates_draft(db, customer, agent, ticket):
    # Ready knowledge exists.
    _ready_knowledge(db, customer)
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    result = _run(job.id)
    assert isinstance(result, dict) and result["status"] == "succeeded"

    db.expire_all()
    job = db.get(AIProcessingJob, job.id)
    assert job.status == "succeeded"
    assert job.error_message is None
    assert job.result and job.result["reply_id"]

    draft = db.get(TicketReply, job.result["reply_id"])
    assert draft is not None
    assert draft.is_ai_suggestion is True
    assert draft.status == "draft"
    assert draft.is_sent is False
    assert draft.content
    assert draft.source_refs  # real source summary persisted


def test_suggestion_source_refs_match_retrieved(db, customer, agent, ticket):
    item = _ready_knowledge(db, customer)
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])
    _run(job.id)

    db.expire_all()
    job = db.get(AIProcessingJob, job.id)
    draft = db.get(TicketReply, job.result["reply_id"])
    refs = draft.source_refs
    assert refs
    assert refs[0]["knowledge_item_id"] == str(item.id)
    assert refs[0]["title"] == item.title
    assert "chunk_index" in refs[0]


def test_suggestion_writes_succeeded_and_created_audit(db, customer, agent, ticket):
    _ready_knowledge(db, customer)
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])
    _run(job.id)

    succeeded = db.query(AuditLog).filter_by(action="reply_suggestion.succeeded").one()
    assert str(succeeded.entity_id) == ticket["id"]
    assert succeeded.new_value["reply_id"]
    created = db.query(AuditLog).filter_by(action="reply.created").one()
    assert created.new_value["is_ai_suggestion"] is True
    assert created.new_value["status"] == "draft"


# --------------------------------------------------------------------------
# Success: no knowledge hit -> conservative escalation
# --------------------------------------------------------------------------

def test_suggestion_no_knowledge_escalates(db, customer, agent, ticket):
    # No ready knowledge at all.
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    # Fake provider default claims "根据知识库说明"; without sources that would
    # be rejected. Use a provider that does not claim a source.
    from backend.app.chat_provider import FakeChatProvider

    provider = FakeChatProvider(
        raw_output=(
            '{"reply": "您好，我们已收到您的反馈，正在为您核实，请稍候。", '
            '"confidence": 0.4, "should_escalate": true, '
            '"reason": "知识库中没有匹配内容，需要人工介入。"}'
        )
    )
    result = _run(job.id, chat_provider=provider)
    assert isinstance(result, dict) and result["status"] == "succeeded"

    db.expire_all()
    job = db.get(AIProcessingJob, job.id)
    draft = db.get(TicketReply, job.result["reply_id"])
    assert draft.content
    assert "根据知识库" not in draft.content
    assert draft.source_refs in (None, []) or draft.source_refs == []


def test_suggestion_no_knowledge_forces_escalation_flag(db, customer, agent, ticket):
    """Without sources, a provider that says should_escalate=false fails."""
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    from backend.app.chat_provider import FakeChatProvider

    provider = FakeChatProvider(
        raw_output=(
            '{"reply": "我可以帮您解决。", "confidence": 0.9, '
            '"should_escalate": false, "reason": "无需升级"}'
        )
    )
    result = _run(job.id, chat_provider=provider)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "failed"
    assert "should_escalate" in row.error_message
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 0


def test_suggestion_no_knowledge_rejects_fake_source_claim(db, customer, agent, ticket):
    """Without sources, claiming '根据知识库' fails."""
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    from backend.app.chat_provider import FakeChatProvider

    provider = FakeChatProvider(
        raw_output=(
            '{"reply": "根据知识库说明，请检查网络。", "confidence": 0.8, '
            '"should_escalate": true, "reason": "有依据"}'
        )
    )
    result = _run(job.id, chat_provider=provider)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "failed"
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 0


# --------------------------------------------------------------------------
# Failure paths
# --------------------------------------------------------------------------

def test_chat_provider_exception_fails_job(db, customer, agent, ticket):
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    provider = FakeChatProvider(
        fail_with=StructuredOutputError("模型输出无法解析"), failures_remaining=1
    )
    result = _run(job.id, chat_provider=provider)
    assert isinstance(result, Exception)

    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "processing"  # RQ retry will re-enter
    assert "无法解析" in row.error_message
    assert row.attempts == 1
    # No half-created draft.
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 0


def test_chat_provider_timeout_fails_job(db, customer, agent, ticket):
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    provider = FakeChatProvider(
        fail_with=ProviderTimeoutError("AI 调用超时"), failures_remaining=1
    )
    result = _run(job.id, chat_provider=provider)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert "超时" in row.error_message


def test_invalid_json_fails_job(db, customer, agent, ticket):
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    provider = FakeChatProvider(raw_output="not json at all")
    result = _run(job.id, chat_provider=provider)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "processing"
    assert "JSON" in row.error_message
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 0


def test_pydantic_validation_failure_fails_job(db, customer, agent, ticket):
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    # Missing reply field -> validation fails.
    provider = FakeChatProvider(
        raw_output='{"confidence": 0.5, "should_escalate": false, "reason": "x"}'
    )
    result = _run(job.id, chat_provider=provider)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "processing"
    assert "校验" in row.error_message


def test_embedding_failure_fails_job(db, customer, agent, ticket):
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    import backend.app.tasks.reply_suggestion as task_mod
    from backend.app import provider_factory
    from backend.app.embedding import EmbeddingTimeoutError

    fail_embedding = FakeEmbeddingProvider(
        fail_with=EmbeddingTimeoutError("embedding 超时"), failures_remaining=1
    )
    original_emb = task_mod.get_embedding_provider
    task_mod.get_embedding_provider = lambda: fail_embedding
    provider_factory._embedding_provider_cache = fail_embedding
    try:
        result = task_mod.run_reply_suggestion(str(job.id))
    except Exception as exc:
        result = exc
    finally:
        task_mod.get_embedding_provider = original_emb

    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "processing"
    assert "超时" in row.error_message
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 0


def test_failure_records_safe_error(db, customer, agent, ticket):
    """Error message must not leak secrets or full content."""
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    provider = FakeChatProvider(raw_output="not json")
    _run(job.id, chat_provider=provider)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    text = (row.error_message or "").lower()
    assert "api_key" not in text
    assert "token" not in text
    assert "password" not in text
    assert len(row.error_message) <= 2000


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

def test_retry_does_not_duplicate_draft(db, customer, agent, ticket):
    """Re-running the task after a success must reuse the draft."""
    _ready_knowledge(db, customer)
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    result1 = _run(job.id)
    assert result1["status"] == "succeeded"
    db.expire_all()
    draft_count_after_first = (
        db.query(TicketReply).filter_by(is_ai_suggestion=True).count()
    )
    assert draft_count_after_first == 1

    # Re-run: job is already succeeded -> early return, no new draft.
    result2 = _run(job.id)
    assert result2["status"] == "succeeded"
    db.expire_all()
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 1


def test_retry_after_partial_success_reuses_draft(db, customer, agent, ticket):
    """A draft created in a previous (killed) run is reused, not duplicated."""
    _ready_knowledge(db, customer)
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])

    # Simulate: a previous execution created the draft but crashed before
    # marking the job succeeded.
    from backend.app.tasks.reply_suggestion import _draft_sender_id

    actor = _draft_sender_id(db, db.get(Ticket, ticket["id"]))
    orphan = TicketReply(
        ticket_id=ticket["id"],
        sender_id=actor,
        content="之前生成的草稿",
        status="draft",
        is_ai_suggestion=True,
        is_sent=False,
        source_refs=[],
    )
    db.add(orphan)
    db.commit()

    result = _run(job.id)
    assert result["status"] == "succeeded"
    db.expire_all()
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 1


def test_succeeded_job_returns_early(db, customer, agent, ticket):
    _ready_knowledge(db, customer)
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])
    _run(job.id)
    db.expire_all()
    job = db.get(AIProcessingJob, job.id)
    before_attempts = job.attempts
    result = _run(job.id)
    assert result["status"] == "succeeded"
    db.expire_all()
    assert db.get(AIProcessingJob, job.id).attempts == before_attempts


# --------------------------------------------------------------------------
# Ticket state safety
# --------------------------------------------------------------------------

def test_suggestion_does_not_change_ticket_state(db, customer, agent, ticket):
    _ready_knowledge(db, customer)
    _ticket_in_review(db, ticket["id"], _user_id(db, agent))
    job = _make_job(db, ticket["id"])
    _run(job.id)
    db.expire_all()
    assert db.get(Ticket, ticket["id"]).status == "in_review"
