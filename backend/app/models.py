"""SQLAlchemy models for the AI Support Workbench.

Field, constraint, index, foreign-key and delete-policy choices follow
docs/data-model.md. All primary keys are UUIDs; timestamps are timezone-aware
and default to ``now()`` at the database level.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import EMBEDDING_DIMENSION
from .database import Base

# Ticket status values enforced by the state machine and database CHECK.
TICKET_STATUSES = ("open", "in_review", "replied", "closed", "canceled")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
TICKET_SENTIMENTS = ("positive", "neutral", "negative")
USER_ROLES = ("customer", "agent", "admin")
KNOWLEDGE_STATUSES = ("processing", "ready", "failed")
KNOWLEDGE_SOURCE_TYPES = ("txt", "pdf")
AI_JOB_TYPES = ("ticket_analysis", "reply_suggestion")
AI_JOB_STATUSES = ("pending", "processing", "succeeded", "failed")


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            "role IN ('customer', 'agent', 'admin')", name="ck_users_role"
        ),
        CheckConstraint(
            "length(email) > 0 AND length(name) > 0", name="ck_users_email_name_not_blank"
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="customer", index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="customer", foreign_keys="Ticket.customer_id"
    )
    assigned_tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="assignee", foreign_keys="Ticket.assignee_id"
    )


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'in_review', 'replied', 'closed', 'canceled')",
            name="ck_tickets_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_tickets_priority",
        ),
        CheckConstraint(
            "sentiment IN ('positive', 'neutral', 'negative')",
            name="ck_tickets_sentiment",
        ),
        CheckConstraint(
            "length(title) > 0 AND length(description) > 0",
            name="ck_tickets_title_description_not_blank",
        ),
        Index("idx_tickets_status", "status"),
        Index("idx_tickets_priority", "priority"),
        Index("idx_tickets_classification", "classification"),
        Index("idx_tickets_sentiment", "sentiment"),
        Index("idx_tickets_customer_id", "customer_id"),
        Index("idx_tickets_assignee_id", "assignee_id"),
        Index("idx_tickets_created_at", "created_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    customer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="normal"
    )
    classification: Mapped[str] = mapped_column(
        String(50), nullable=False, default="", server_default=""
    )
    summary: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    sentiment: Mapped[str] = mapped_column(
        String(20), nullable=False, default="neutral", server_default="neutral"
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    customer: Mapped["User"] = relationship(
        back_populates="tickets", foreign_keys=[customer_id]
    )
    assignee: Mapped["User | None"] = relationship(
        back_populates="assigned_tickets", foreign_keys=[assignee_id]
    )
    replies: Mapped[list["TicketReply"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", passive_deletes=True
    )
    evaluation: Mapped["Evaluation | None"] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", passive_deletes=True
    )
    ai_jobs: Mapped[list["AIProcessingJob"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", passive_deletes=True
    )


class TicketReply(Base):
    __tablename__ = "ticket_replies"
    __table_args__ = (
        CheckConstraint("length(content) > 0", name="ck_ticket_replies_content_not_blank"),
        Index("idx_ticket_replies_ticket_id", "ticket_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    ticket_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_ai_suggestion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = _created_at()

    ticket: Mapped["Ticket"] = relationship(back_populates="replies")


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'ready', 'failed')",
            name="ck_knowledge_items_status",
        ),
        CheckConstraint(
            "source_type IN ('txt', 'pdf')", name="ck_knowledge_items_source_type"
        ),
        CheckConstraint(
            "file_size_bytes > 0", name="ck_knowledge_items_file_size_positive"
        ),
        Index("idx_knowledge_items_status", "status"),
        Index("idx_knowledge_items_created_at", "created_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="processing", server_default="processing"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="knowledge_item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_item_id", "chunk_index",
            name="uq_knowledge_chunks_item_chunk_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index_non_negative"),
        CheckConstraint(
            "length(trim(content)) > 0", name="ck_knowledge_chunks_content_not_blank"
        ),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    knowledge_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()

    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="chunks")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "length(action) > 0 AND length(entity_type) > 0",
            name="ck_audit_logs_not_blank",
        ),
        Index("idx_audit_logs_actor_id", "actor_id"),
        Index("idx_audit_logs_action", "action"),
        Index("idx_audit_logs_entity_type", "entity_type"),
        Index("idx_audit_logs_entity_id", "entity_id"),
        Index("idx_audit_logs_created_at", "created_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    actor_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_evaluations_ticket_id"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_evaluations_rating_range"),
        Index("idx_evaluations_customer_id", "customer_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    ticket_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    ticket: Mapped["Ticket"] = relationship(back_populates="evaluation")


class AIProcessingJob(Base):
    __tablename__ = "ai_processing_jobs"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id", "job_type", name="uq_ai_jobs_ticket_job_type"
        ),
        CheckConstraint(
            "job_type IN ('ticket_analysis', 'reply_suggestion')",
            name="ck_ai_jobs_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed')",
            name="ck_ai_jobs_status",
        ),
        Index("idx_ai_jobs_status", "status"),
    )

    id: Mapped[UUID] = _uuid_pk()
    ticket_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)
    business_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="ai_jobs")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("key", name="uq_idempotency_keys_key"),
        CheckConstraint("length(key) > 0", name="ck_idempotency_keys_key_not_blank"),
        Index("idx_idempotency_keys_actor_id", "actor_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()
