"""Database model tests: schema, constraints, cascades and indexes.

These run against the isolated *_test PostgreSQL database created in
``conftest.py`` (mirror of docs/data-model.md).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.models import (
    AIProcessingJob,
    AuditLog,
    Evaluation,
    IdempotencyKey,
    KnowledgeChunk,
    KnowledgeItem,
    Ticket,
    TicketReply,
    User,
)


def _make_user(email="customer@example.com", role="customer") -> User:
    return User(
        email=email,
        password_hash="not-a-real-hash",
        name="测试用户",
        role=role,
    )


def _make_ticket(user: User) -> Ticket:
    return Ticket(
        customer_id=user.id,
        title="无法登录账号",
        description="登录时提示密码错误，但密码是正确的。",
    )


def _make_knowledge_item(user: User) -> KnowledgeItem:
    return KnowledgeItem(
        title="产品手册",
        content="这是产品手册的全文。",
        source_type="txt",
        file_name="手册.txt",
        file_size_bytes=128,
        uploaded_by=user.id,
        status="ready",
    )


# --------------------------------------------------------------------------
# Table presence
# --------------------------------------------------------------------------

def test_all_expected_tables_exist(db):
    rows = db.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    ).scalars().all()
    expected = {
        "users",
        "tickets",
        "ticket_replies",
        "knowledge_items",
        "knowledge_chunks",
        "audit_logs",
        "evaluations",
        "ai_processing_jobs",
        "idempotency_keys",
    }
    assert expected <= set(rows)


def test_pgvector_extension_enabled(db):
    rows = db.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    ).scalars().all()
    assert "vector" in rows


# --------------------------------------------------------------------------
# Ticket status / priority / sentiment CHECK constraints
# --------------------------------------------------------------------------

def test_ticket_valid_status_defaults(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    db.add(ticket)
    db.flush()
    assert ticket.status == "open"
    assert ticket.priority == "normal"
    assert ticket.sentiment == "neutral"


def test_ticket_rejects_invalid_status(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    ticket.status = "banana"
    db.add(ticket)
    with pytest.raises(IntegrityError):
        db.flush()


def test_ticket_rejects_invalid_priority(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    ticket.priority = "extreme"
    db.add(ticket)
    with pytest.raises(IntegrityError):
        db.flush()


def test_ticket_rejects_blank_title(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = Ticket(
        customer_id=user.id,
        title="",
        description="非空描述",
    )
    db.add(ticket)
    with pytest.raises(IntegrityError):
        db.flush()


def test_user_rejects_invalid_role(db):
    db.add(_make_user(email="agent@example.com", role="adminx"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_knowledge_item_rejects_invalid_status(db):
    user = _make_user()
    db.add(user)
    db.flush()
    item = _make_knowledge_item(user)
    item.status = "stale"
    db.add(item)
    with pytest.raises(IntegrityError):
        db.flush()


def test_ai_job_rejects_invalid_type(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    db.add(ticket)
    db.flush()
    job = AIProcessingJob(
        ticket_id=ticket.id,
        job_type="summarize_everything",
        business_key=f"ticket_analysis:{ticket.id}",
    )
    db.add(job)
    with pytest.raises(IntegrityError):
        db.flush()


# --------------------------------------------------------------------------
# Unique constraints
# --------------------------------------------------------------------------

def test_user_email_unique(db):
    db.add(_make_user())
    db.flush()
    db.add(_make_user())
    with pytest.raises(IntegrityError):
        db.flush()


def test_evaluation_one_per_ticket(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    db.add(ticket)
    db.flush()
    db.add(Evaluation(ticket_id=ticket.id, customer_id=user.id, rating=5))
    db.flush()
    db.add(Evaluation(ticket_id=ticket.id, customer_id=user.id, rating=3))
    with pytest.raises(IntegrityError):
        db.flush()


def test_ai_job_unique_per_ticket_and_type(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    db.add(ticket)
    db.flush()
    db.add(AIProcessingJob(
        ticket_id=ticket.id,
        job_type="ticket_analysis",
        business_key=f"ticket_analysis:{ticket.id}",
    ))
    db.flush()
    db.add(AIProcessingJob(
        ticket_id=ticket.id,
        job_type="ticket_analysis",
        business_key=f"ticket_analysis:{ticket.id}",
    ))
    with pytest.raises(IntegrityError):
        db.flush()


def test_knowledge_chunk_unique_item_and_index(db):
    user = _make_user()
    db.add(user)
    db.flush()
    item = _make_knowledge_item(user)
    db.add(item)
    db.flush()
    db.add(KnowledgeChunk(
        knowledge_item_id=item.id,
        chunk_index=0,
        content="第一段",
        embedding=[0.0] * 1536,
    ))
    db.flush()
    db.add(KnowledgeChunk(
        knowledge_item_id=item.id,
        chunk_index=0,
        content="重复段",
        embedding=[1.0] * 1536,
    ))
    with pytest.raises(IntegrityError):
        db.flush()


# --------------------------------------------------------------------------
# Foreign keys and delete behavior
# --------------------------------------------------------------------------

def test_knowledge_item_deletion_cascades_to_chunks(db):
    user = _make_user()
    db.add(user)
    db.flush()
    item = _make_knowledge_item(user)
    db.add(item)
    db.flush()
    db.add(KnowledgeChunk(
        knowledge_item_id=item.id,
        chunk_index=0,
        content="第一段",
        embedding=[0.5] * 1536,
    ))
    db.flush()
    db.delete(item)
    db.commit()
    remaining = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count()
    assert remaining == 0


def test_ticket_deletion_cascades_to_replies_and_jobs(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    db.add(ticket)
    db.flush()
    db.add(TicketReply(
        ticket_id=ticket.id,
        sender_id=user.id,
        content="您好，请提供账号邮箱。",
        is_sent=True,
    ))
    db.add(AIProcessingJob(
        ticket_id=ticket.id,
        job_type="ticket_analysis",
        business_key=f"ticket_analysis:{ticket.id}",
        status="succeeded",
    ))
    db.flush()
    db.delete(ticket)
    db.commit()
    assert db.query(TicketReply).filter_by(ticket_id=ticket.id).count() == 0
    assert db.query(AIProcessingJob).filter_by(ticket_id=ticket.id).count() == 0


def test_ticket_customer_delete_restricted(db):
    """Deleting a customer with tickets must be blocked by RESTRICT."""
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    db.add(ticket)
    db.flush()
    db.delete(user)
    with pytest.raises(IntegrityError):
        db.commit()


def test_ticket_assignee_delete_sets_null(db):
    customer = _make_user(email="customer@example.com")
    agent = _make_user(email="agent@example.com", role="agent")
    db.add_all([customer, agent])
    db.flush()
    ticket = _make_ticket(customer)
    ticket.assignee_id = agent.id
    db.add(ticket)
    db.flush()
    db.delete(agent)
    db.commit()
    db.refresh(ticket)
    assert ticket.assignee_id is None


# --------------------------------------------------------------------------
# Vector index
# --------------------------------------------------------------------------

def test_hnsw_index_exists(db):
    rows = db.execute(text(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE tablename = 'knowledge_chunks' AND indexname = 'ix_knowledge_chunks_embedding_hnsw'"
    )).all()
    assert len(rows) == 1
    assert "hnsw" in rows[0].indexdef.lower()
    assert "vector_cosine_ops" in rows[0].indexdef


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

def test_ai_job_defaults(db):
    user = _make_user()
    db.add(user)
    db.flush()
    ticket = _make_ticket(user)
    db.add(ticket)
    db.flush()
    job = AIProcessingJob(
        ticket_id=ticket.id,
        job_type="reply_suggestion",
        business_key=f"reply_suggestion:{ticket.id}",
    )
    db.add(job)
    db.flush()
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.max_attempts == 3
