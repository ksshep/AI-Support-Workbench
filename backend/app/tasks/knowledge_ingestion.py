"""RQ task: ``knowledge_ingestion`` (W3-B).

Runs inside the RQ worker process. Reads the raw file bytes stored on the
``knowledge_items`` row, parses (TXT/PDF), chunks, embeds, validates the
vector dimension, and writes ``knowledge_chunks`` — then moves the item to
``ready``. On any failure the whole transaction rolls back, the item becomes
``failed`` with an ``error_message``, and the provider error is re-raised so
RQ applies its retry policy.

Status flow: ``processing -> ready``, or ``processing -> failed`` after the
retry budget is exhausted. ``knowledge_items.status`` is the durable source of
truth — never an in-memory dictionary — so a worker restart cannot fake state.
"""

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..embedding import (
    EmbeddingDimensionError,
    EmbeddingProvider,
    EmbeddingProviderError,
)
from ..models import KnowledgeChunk, KnowledgeItem
from ..provider_factory import get_embedding_provider
from ..text_processing import (
    TextProcessingError,
    extract_pages,
    split_chunks,
)

MAX_ERROR_MESSAGE_LENGTH = 2000
# Chunking configuration (kept in sync with the service defaults).
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _fail_item(db: Session, item: KnowledgeItem, message: str) -> None:
    """Move an item to failed, recording the error message.

    Only touches the item row — any previously committed chunks of an earlier
    successful run are untouched (the item was still ``ready`` and should not
    be degraded by a failed re-ingestion).
    """
    item.status = "failed"
    item.error_message = (message or "")[:MAX_ERROR_MESSAGE_LENGTH]
    db.commit()


def run_knowledge_ingestion(item_id: str) -> dict:
    """RQ job body: ingest one knowledge document.

    Reads the item fresh in a new session (the worker is a separate process),
    deletes any pre-existing chunks for a clean re-ingestion, then parses,
    chunks and embeds. Every insert commits in one transaction; a failure
    rolls the chunk inserts back and marks the item failed.
    """
    from ..database import SessionLocal

    with SessionLocal() as db:
        item = db.get(KnowledgeItem, UUID(item_id))
        if item is None:
            raise ValueError(f"知识库文档不存在: {item_id}")
        if item.status == "ready":
            return {"status": item.status}

        if item.file_content is None:
            db.rollback()
            _fail_item(db, item, "缺少文件内容，无法处理")
            raise ValueError("file_content missing")

        try:
            # 1. Parse the raw bytes into per-page text.
            pages = extract_pages(
                item.source_type, item.file_content, file_name=item.file_name
            )
            # 2. Chunk with a continuous index and page numbers.
            chunks = split_chunks(
                pages, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP
            )
            # 3. Embed every chunk content in one batch and validate the
            #    batch (one vector per text, database dimension) so a bad
            #    provider can never write mismatched vectors.
            provider = get_embedding_provider()
            vectors = provider.embed_texts([chunk.content for chunk in chunks])
            EmbeddingProvider.validate_batch(
                [chunk.content for chunk in chunks], vectors
            )
            # 4. Store the extracted full text on the item.
            item.content = "\n\n".join(page.text for page in pages)

            # 5. Remove stale chunks so a re-ingestion never accumulates.
            db.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.knowledge_item_id == item.id
                )
            )

            # 6. Insert chunks + commit together.
            for chunk, vector in zip(chunks, vectors):
                db.add(
                    KnowledgeChunk(
                        knowledge_item_id=item.id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        page_number=chunk.page_number,
                        embedding=vector,
                    )
                )
            item.status = "ready"
            item.error_message = None
            db.commit()
        except (TextProcessingError, EmbeddingProviderError, ValueError) as exc:
            # Text/embedding/data failures: roll back chunk inserts and mark
            # the item failed, then let RQ decide about retries.
            db.rollback()
            _fail_item(db, item, str(exc) or exc.__class__.__name__)
            raise

        db.refresh(item)
        return {"status": item.status}
