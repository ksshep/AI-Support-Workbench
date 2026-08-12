"""RAG context builder and knowledge-ingestion task tests (W3-B).

The ingestion task is run directly (not through a live worker) with the Fake
Embedding Provider so the whole parse -> chunk -> embed -> persist -> ready
flow is exercised against the real test database offline. Failure paths
(parse failure, embedding failure, dimension mismatch, empty document) are
covered and each leaves the item ``failed`` with an ``error_message``.
"""

from backend.app.embedding import FakeEmbeddingProvider
from backend.app.models import KnowledgeChunk, KnowledgeItem
from backend.app.services.knowledge_service import build_knowledge_context
from backend.app.tasks.knowledge_ingestion import run_knowledge_ingestion
from tests.fixtures import make_pdf_bytes, sample_txt_bytes


def _make_item(db, customer, file_name="guide.txt", content=None, source_type="txt"):
    from backend.app.models import User

    uploader = db.query(User).filter_by(email=customer["email"]).one()
    raw = content if content is not None else sample_txt_bytes()
    item = KnowledgeItem(
        title="测试文档",
        content="",
        source_type=source_type,
        file_name=file_name,
        file_size_bytes=max(1, len(raw)),
        file_content=raw,
        status="processing",
        uploaded_by=uploader.id,
    )
    db.add(item)
    db.flush()
    db.commit()
    db.refresh(item)
    return item


def _run(db, item, provider=None):
    import backend.app.tasks.knowledge_ingestion as task_mod
    from backend.app import provider_factory

    provider = provider or FakeEmbeddingProvider()
    original = task_mod.get_embedding_provider
    task_mod.get_embedding_provider = lambda: provider
    provider_factory._embedding_provider_cache = provider
    try:
        return run_knowledge_ingestion(str(item.id))
    except Exception as exc:
        return exc
    finally:
        task_mod.get_embedding_provider = original


# --------------------------------------------------------------------------
# Ingestion: success
# --------------------------------------------------------------------------

def test_ingestion_txt_to_ready(db, customer):
    item = _make_item(
        db, customer, content="第一段内容。\n第二段内容。".encode("utf-8")
    )
    result = _run(db, item)
    assert isinstance(result, dict)
    assert result["status"] == "ready"
    db.expire_all()
    row = db.get(KnowledgeItem, item.id)
    assert row.status == "ready"
    assert row.error_message is None
    assert row.content  # extracted full text stored
    chunks = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).all()
    assert len(chunks) >= 1


def test_ingestion_pdf_skips_blank_pages(db, customer):
    item = _make_item(db, customer, file_name="manual.pdf",
                      content=make_pdf_bytes(["Page A text", "", "Page C text"]),
                      source_type="pdf")
    result = _run(db, item)
    assert isinstance(result, dict) and result["status"] == "ready"
    db.expire_all()
    chunks = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).all()
    assert len(chunks) >= 2
    assert all(c.page_number in (1, 3) for c in chunks)


def test_ingestion_chunk_index_continuous(db, customer):
    long_text = ("段落" * 300).encode("utf-8")
    item = _make_item(db, customer, content=long_text)
    _run(db, item)
    db.expire_all()
    chunks = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).all()
    indexes = sorted(c.chunk_index for c in chunks)
    assert indexes == list(range(len(chunks)))


def test_ingestion_embedding_count_and_dimension(db, customer):
    item = _make_item(db, customer)
    _run(db, item)
    db.expire_all()
    chunks = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).all()
    assert len(chunks) >= 1
    from backend.app.config import EMBEDDING_DIMENSION

    assert all(len(c.embedding) == EMBEDDING_DIMENSION for c in chunks)


def test_reingestion_cleans_old_chunks(db, customer):
    """A failed-then-retried ingestion must not accumulate duplicate chunks."""
    item = _make_item(db, customer)
    result = _run(db, item)
    assert result["status"] == "ready"
    db.expire_all()
    first_count = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count()
    assert first_count >= 1

    # Force a re-ingestion (worker re-entry) — status ready is a no-op in the
    # task, so reset it to processing first to simulate a retry.
    item = db.get(KnowledgeItem, item.id)
    item.status = "processing"
    db.commit()
    result = _run(db, item)
    assert result["status"] == "ready"
    db.expire_all()
    second_count = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count()
    assert second_count == first_count


# --------------------------------------------------------------------------
# Ingestion: failure paths
# --------------------------------------------------------------------------

def test_empty_document_fails(db, customer):
    item = _make_item(db, customer, content=b"   \n ")
    result = _run(db, item)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(KnowledgeItem, item.id)
    assert row.status == "failed"
    assert row.error_message
    assert db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count() == 0


def test_broken_pdf_fails(db, customer):
    item = _make_item(db, customer, file_name="bad.pdf", content=b"not a pdf", source_type="pdf")
    result = _run(db, item)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(KnowledgeItem, item.id)
    assert row.status == "failed"
    assert row.error_message


def test_embedding_failure_rolls_back(db, customer):
    from backend.app.embedding import EmbeddingTimeoutError

    item = _make_item(db, customer)
    provider = FakeEmbeddingProvider(
        fail_with=EmbeddingTimeoutError("超时"), failures_remaining=1
    )
    result = _run(db, item, provider)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(KnowledgeItem, item.id)
    assert row.status == "failed"
    assert "超时" in row.error_message
    assert db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count() == 0


def test_dimension_mismatch_rolls_back(db, customer):
    from backend.app.embedding import EmbeddingDimensionError

    item = _make_item(db, customer)
    # A provider that yields wrong-dimension vectors; the task must reject
    # them before inserting (the fake validates via validate_batch).
    provider = FakeEmbeddingProvider(dimension_override=8)
    result = _run(db, item, provider)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(KnowledgeItem, item.id)
    assert row.status == "failed"
    assert "维度" in row.error_message
    assert db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count() == 0


def test_missing_file_content_fails(db, customer):
    from backend.app.models import User

    uploader = db.query(User).filter_by(email=customer["email"]).one()
    item = KnowledgeItem(
        title="无文件",
        content="",
        source_type="txt",
        file_name="x.txt",
        file_size_bytes=1,
        file_content=None,  # no raw bytes
        status="processing",
        uploaded_by=uploader.id,
    )
    db.add(item)
    db.commit()
    result = _run(db, item)
    assert isinstance(result, Exception)
    db.expire_all()
    row = db.get(KnowledgeItem, item.id)
    assert row.status == "failed"
    assert "文件内容" in row.error_message


def test_already_ready_is_noop(db, customer):
    item = _make_item(db, customer)
    _run(db, item)
    db.expire_all()
    item = db.get(KnowledgeItem, item.id)
    first = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count()
    # Running again on a ready item returns early.
    result = _run(db, item)
    assert result["status"] == "ready"
    db.expire_all()
    second = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item.id).count()
    assert second == first


# --------------------------------------------------------------------------
# RAG context builder
# --------------------------------------------------------------------------

def test_context_builds_with_sources():
    results = [
        {"title": "手册", "chunk_index": 0, "content": "第一段内容"},
        {"title": "手册", "chunk_index": 1, "content": "第二段内容"},
    ]
    ctx = build_knowledge_context(results, max_chars=10_000)
    assert "[手册 | 第 1 段]" in ctx
    assert "第一段内容" in ctx
    assert "[手册 | 第 2 段]" in ctx
    assert "第二段内容" in ctx


def test_context_empty_for_no_results():
    assert build_knowledge_context([]) == ""


def test_context_respects_max_chars():
    results = [
        {"title": "A", "chunk_index": 0, "content": "x" * 800},
        {"title": "B", "chunk_index": 1, "content": "y" * 800},
    ]
    ctx = build_knowledge_context(results, max_chars=1000)
    assert len(ctx) <= 1000
    # First snippet included, second dropped because it would overflow.
    assert "第 1 段" in ctx
    assert "第 2 段" not in ctx


def test_context_does_not_fabricate_sources():
    results = [{"title": "真实来源", "chunk_index": 0, "content": "真实内容"}]
    ctx = build_knowledge_context(results)
    assert "真实来源" in ctx
    assert "真实内容" in ctx
    assert "不存在" not in ctx
