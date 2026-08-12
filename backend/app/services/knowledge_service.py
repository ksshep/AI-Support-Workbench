"""Knowledge base business logic — request path (W3-B).

The web process only *schedules* ingestion: it validates the uploaded file,
stores the raw bytes on the ``knowledge_items`` row, and hands the heavy work
(parse -> chunk -> embed -> insert vectors) to the RQ worker. The upload
endpoint therefore returns 201 immediately with ``status=processing``.

Permission model (enforced here, not in the router):
- admin: upload / view / delete any knowledge item;
- agent: view ``ready`` items and run search; cannot delete or upload;
- customer: cannot manage the knowledge base at all (403).

Concurrency / idempotency: a stable RQ ``job_id`` per item plus the DB as the
source of truth for ``status`` mean a document is never ingested twice and
deleting an item cascades away its chunks and cancels/purges the pending job.
"""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from redis import Redis
from rq import Queue, Retry
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import (
    RQ_MAX_RETRIES,
    RQ_RETRY_DELAYS,
    get_redis_url,
)
from ..models import KnowledgeChunk, KnowledgeItem, User
from . import audit as audit_service

# Stable RQ job id (dash separator: RQ rejects colons in job ids).
KNOWLEDGE_JOB_PREFIX = "knowledge_ingestion-"
# Fully qualified path to the RQ job body; workers import it by name.
KNOWLEDGE_INGESTION_TASK_PATH = (
    "backend.app.tasks.knowledge_ingestion.run_knowledge_ingestion"
)

# Upload limits.
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_SOURCE_TYPES = ("txt", "pdf")
MAX_TITLE_LENGTH = 255

# Chunking configuration used by the ingestion task.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


class KnowledgePermissionError(Exception):
    """Raised when a role may not perform the requested operation."""


def _get_item_or_404(db: Session, item_id: UUID) -> KnowledgeItem:
    item = db.scalar(
        select(KnowledgeItem).where(KnowledgeItem.id == item_id)
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "知识库文档不存在"},
        )
    return item


def _require_admin(current_user) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "只有管理员可以管理知识库"},
        )


def _can_view(item: KnowledgeItem, current_user) -> None:
    """Agents may view ready items; admins may view everything."""
    if current_user.role == "admin":
        return
    if current_user.role == "agent" and item.status == "ready":
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "forbidden", "message": "无权访问该知识库文档"},
    )


def _redis_client() -> Redis:
    return Redis.from_url(get_redis_url())


def _enqueue_ingestion(item: KnowledgeItem) -> None:
    """Enqueue ``knowledge_ingestion`` with a stable job_id.

    If a job for the same id is still queued/scheduled, RQ returns the
    existing one and nothing new is enqueued. Queue errors are swallowed: the
    ``knowledge_items.status`` column is the durable source of truth and a
    later re-upload can retry.
    """
    redis = _redis_client()
    queue = Queue("default", connection=redis)
    job_id = f"{KNOWLEDGE_JOB_PREFIX}{item.id}"
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
            KNOWLEDGE_INGESTION_TASK_PATH,
            str(item.id),
            job_id=job_id,
            retry=Retry(max=RQ_MAX_RETRIES, interval=RQ_RETRY_DELAYS),
        )
    except Exception:
        # The database row is the source of truth; losing the enqueue must not
        # break the upload response.
        pass


def validate_upload(
    *,
    file_name: str,
    raw: bytes,
    title: str | None,
) -> tuple[str, str]:
    """Validate the uploaded file and derive (title, source_type).

    Returns ``(safe_title, source_type)`` or raises a 400/422 HTTP error.
    """
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "上传文件为空"},
        )
    if len(raw) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "文件不能超过 20MB"},
        )

    lower_name = file_name.lower()
    if lower_name.endswith(".txt"):
        source_type = "txt"
    elif lower_name.endswith(".pdf"):
        source_type = "pdf"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_input",
                "message": "只支持 txt 或 pdf 文件",
            },
        )

    safe_title = (title or "").strip() or _title_from_file_name(file_name)
    if len(safe_title) > MAX_TITLE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "标题不能超过 255 字符"},
        )
    return safe_title, source_type


def _title_from_file_name(file_name: str) -> str:
    # Strip the extension for a readable default title.
    base = file_name.rsplit(".", 1)[0].strip()
    return base or "未命名文档"


def create_knowledge_item(
    db: Session,
    *,
    file_name: str,
    raw: bytes,
    title: str | None,
    current_user,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Create a ``processing`` knowledge item and enqueue ingestion.

    The raw bytes are stored on the row so the worker (a separate container)
    can read the original file. Returns the 201 response payload.
    """
    _require_admin(current_user)
    safe_title, source_type = validate_upload(
        file_name=file_name, raw=raw, title=title
    )

    item = KnowledgeItem(
        title=safe_title,
        content="",  # filled by the worker after parsing
        source_type=source_type,
        file_name=file_name,
        file_size_bytes=len(raw),
        file_content=raw,
        status="processing",
        uploaded_by=current_user.id,
    )
    db.add(item)
    try:
        db.flush()
    except Exception:
        db.rollback()
        raise

    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="knowledge.uploaded",
        entity_type="knowledge_item",
        entity_id=item.id,
        new_value={
            "title": item.title,
            "source_type": item.source_type,
            "file_size_bytes": item.file_size_bytes,
            "status": "processing",
        },
        ip_address=ip_address,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(item)

    _enqueue_ingestion(item)

    return {
        "id": str(item.id),
        "title": item.title,
        "source_type": item.source_type,
        "file_name": item.file_name,
        "file_size_bytes": item.file_size_bytes,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
    }


def list_knowledge_items(
    db: Session,
    *,
    current_user,
    page: int,
    page_size: int,
    status_filter: str | None,
) -> dict[str, Any]:
    """Paginated, role-scoped knowledge-item list.

    admin: all items; agent: only ``ready``; customer: 403. Ordered by
    ``created_at`` descending.
    """
    if current_user.role == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "客户不能管理知识库"},
        )

    query = select(KnowledgeItem).order_by(KnowledgeItem.created_at.desc())
    count_query = select(func.count(KnowledgeItem.id))

    if current_user.role == "agent":
        query = query.where(KnowledgeItem.status == "ready")
        count_query = count_query.where(KnowledgeItem.status == "ready")
    if status_filter is not None:
        query = query.where(KnowledgeItem.status == status_filter)
        count_query = count_query.where(KnowledgeItem.status == status_filter)

    total = db.scalar(count_query) or 0
    rows = db.scalars(
        query.limit(page_size).offset((page - 1) * page_size)
    ).all()

    uploader_ids = list({item.uploaded_by for item in rows})
    names = {}
    if uploader_ids:
        name_rows = db.execute(
            select(User.id, User.name).where(User.id.in_(uploader_ids))
        ).all()
        names = {str(uid): name for uid, name in name_rows}

    items = [
        {
            "id": str(item.id),
            "title": item.title,
            "source_type": item.source_type,
            "file_name": item.file_name,
            "file_size_bytes": item.file_size_bytes,
            "status": item.status,
            "error_message": item.error_message,
            "uploaded_by": str(item.uploaded_by),
            "uploader_name": names.get(str(item.uploaded_by), ""),
            "created_at": item.created_at.isoformat(),
        }
        for item in rows
    ]
    pages = max(1, -(-total // page_size)) if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def get_knowledge_item(
    db: Session,
    *,
    item_id: UUID,
    current_user,
) -> dict[str, Any]:
    """Return one item's detail incl. chunk count and embedding count.

    admin: any; agent: ready only; customer: 403. The raw file bytes are
    never returned.
    """
    item = _get_item_or_404(db, item_id)
    _can_view(item, current_user)

    chunk_count = db.scalar(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.knowledge_item_id == item.id
        )
    ) or 0
    embedding_count = db.scalar(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.knowledge_item_id == item.id,
            KnowledgeChunk.embedding.is_not(None),
        )
    ) or 0

    uploader = db.get(User, item.uploaded_by)
    return {
        "id": str(item.id),
        "title": item.title,
        "source_type": item.source_type,
        "file_name": item.file_name,
        "file_size_bytes": item.file_size_bytes,
        "status": item.status,
        "error_message": item.error_message,
        "uploaded_by": str(item.uploaded_by),
        "uploader_name": uploader.name if uploader else "",
        "chunk_count": chunk_count,
        "embedding_count": embedding_count,
        "created_at": item.created_at.isoformat(),
    }


def delete_knowledge_item(
    db: Session,
    *,
    item_id: UUID,
    current_user,
    ip_address: str | None = None,
) -> None:
    """Delete an item, cascading its chunks, and audit the deletion.

    Only admins may delete. The ORM relationship cascade (``all,
    delete-orphan``, ``passive_deletes=True``) removes chunks in the same
    transaction as the item row; if anything fails the whole transaction
    rolls back and nothing is left half-deleted.
    """
    _require_admin(current_user)
    item = _get_item_or_404(db, item_id)

    audit_service.create_audit_log(
        db,
        actor_id=current_user.id,
        action="knowledge.deleted",
        entity_type="knowledge_item",
        entity_id=item.id,
        old_value={
            "title": item.title,
            "source_type": item.source_type,
            "status": item.status,
        },
        ip_address=ip_address,
    )
    db.delete(item)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def search_knowledge(
    db: Session,
    *,
    query: str,
    top_k: int,
    current_user,
) -> dict[str, list[dict[str, Any]]]:
    """Vector-similarity search over ready chunks (agent/admin).

    Only chunks whose item is ``ready`` and which have an embedding are
    returned, ordered by cosine similarity descending. ``query`` and
    ``top_k`` are validated by the Pydantic schema before this is called.
    """
    if current_user.role == "customer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "客户不能检索知识库"},
        )

    from ..provider_factory import get_embedding_provider

    provider = get_embedding_provider()
    query_vector = provider.embed_texts([query])[0]

    # pgvector cosine distance operator (<=>) over ready items only.
    rows = db.execute(
        select(
            KnowledgeChunk.id,
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
        .limit(top_k)
    ).all()

    items = [
        {
            "chunk_id": str(row.id),
            "content": row.content,
            "knowledge_item_id": str(row.knowledge_item_id),
            "title": row.title,
            "chunk_index": row.chunk_index,
            "page_number": row.page_number,
            # Cosine distance can exceed 1 when the angle is > 90°; clamp the
            # derived similarity to [0, 1] so consumers always get a valid
            # score.
            "similarity_score": round(
                min(1.0, max(0.0, 1.0 - float(row.distance))), 6
            ),
        }
        for row in rows
    ]
    return {"items": items}


def build_knowledge_context(
    results: list[dict[str, Any]], max_chars: int = 2000
) -> str:
    """Build a RAG context string from search results (for W4-A).

    Each snippet is prefixed with its source (``[title | 第 N 段]``) so a
    generated reply can cite a real source. Results are already sorted by
    similarity; the builder keeps adding snippets until ``max_chars`` is
    reached. Returns an empty string when there is no result — never invents
    a source.
    """
    if not results:
        return ""
    parts: list[str] = []
    used = 0
    for result in results:
        snippet = f"[{result.get('title', '未知来源')} | 第 {result.get('chunk_index', 0) + 1} 段]\n{result.get('content', '')}"
        if used + len(snippet) > max_chars:
            break
        parts.append(snippet)
        used += len(snippet)
    return "\n\n".join(parts)
