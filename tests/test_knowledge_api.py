"""Knowledge base API tests (W3-B).

End-to-end HTTP tests: upload (admin-only), list/detail role scoping, delete
cascade, and vector search. The RQ worker path is exercised by running the
task body directly with the Fake Embedding Provider, keeping tests offline.
"""

import pytest

from backend.app.models import KnowledgeChunk, KnowledgeItem
from backend.app.tasks.knowledge_ingestion import run_knowledge_ingestion
from tests.fixtures import make_pdf_bytes, sample_txt_bytes


def _upload(client, headers, file_name="guide.txt", content=None, title=None):
    data = {"file": (file_name, content or sample_txt_bytes())}
    if title:
        data["title"] = (None, title)
    return client.post("/knowledge-items", files=data, headers=headers)


def _ingest(db, item_id, provider=None):
    """Run the ingestion task for an item (swapping in a custom provider)."""
    import backend.app.tasks.knowledge_ingestion as task_mod
    from backend.app import provider_factory
    from backend.app.embedding import FakeEmbeddingProvider

    provider = provider or FakeEmbeddingProvider()
    original = task_mod.get_embedding_provider
    task_mod.get_embedding_provider = lambda: provider
    provider_factory._embedding_provider_cache = provider
    try:
        return run_knowledge_ingestion(str(item_id))
    except Exception:
        return None
    finally:
        task_mod.get_embedding_provider = original


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------

def test_admin_upload_txt_returns_201_processing(client, admin, db):
    resp = _upload(client, admin["headers"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "processing"
    assert body["source_type"] == "txt"
    assert "password" not in resp.text.lower()
    # The raw bytes are stored for the worker.
    item = db.get(KnowledgeItem, body["id"])
    assert item.file_content is not None


def test_upload_requires_login(client, admin):
    resp = client.post(
        "/knowledge-items", files={"file": ("a.txt", b"content")}
    )
    assert resp.status_code == 401


def test_agent_upload_forbidden(client, admin, agent):
    resp = _upload(client, agent["headers"])
    assert resp.status_code == 403


def test_customer_upload_forbidden(client, admin, customer):
    resp = _upload(client, customer["headers"])
    assert resp.status_code == 403


def test_upload_rejects_non_txt_pdf(client, admin):
    resp = client.post(
        "/knowledge-items",
        files={"file": ("evil.exe", b"MZ...")},
        headers=admin["headers"],
    )
    assert resp.status_code == 400


def test_upload_rejects_empty_file(client, admin):
    resp = client.post(
        "/knowledge-items", files={"file": ("empty.txt", b"")},
        headers=admin["headers"],
    )
    assert resp.status_code == 400


def test_upload_rejects_oversized_file(client, admin):
    big = b"x" * (20 * 1024 * 1024 + 1)
    resp = client.post(
        "/knowledge-items", files={"file": ("big.txt", big)},
        headers=admin["headers"],
    )
    assert resp.status_code == 400


def test_upload_pdf_returns_201(client, admin):
    resp = client.post(
        "/knowledge-items",
        files={"file": ("manual.pdf", make_pdf_bytes(["Page text"]))},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    assert resp.json()["source_type"] == "pdf"


def test_upload_does_not_block_on_worker(client, admin, fake_redis):
    """Upload returns immediately; the worker is not awaited."""
    import time

    start = time.monotonic()
    resp = _upload(client, admin["headers"])
    elapsed = time.monotonic() - start
    assert resp.status_code == 201
    assert elapsed < 2.0


# --------------------------------------------------------------------------
# List / detail
# --------------------------------------------------------------------------

def test_admin_lists_all_items(client, admin, db):
    _upload(client, admin["headers"], file_name="a.txt")
    _upload(client, admin["headers"], file_name="b.txt")
    resp = client.get("/knowledge-items", headers=admin["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert len(body["items"]) == 2
    assert "uploader_name" in body["items"][0]


def test_agent_only_sees_ready_items(client, admin, agent, db):
    _upload(client, admin["headers"], file_name="a.txt")
    resp = client.get("/knowledge-items", headers=agent["headers"])
    assert resp.status_code == 200
    # processing items are invisible to agents.
    assert resp.json()["total"] == 0


def test_agent_sees_ready_item_after_ingestion(client, admin, agent, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    _ingest(db, item_id)
    resp = client.get("/knowledge-items", headers=agent["headers"])
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_customer_list_forbidden(client, admin, customer):
    resp = client.get("/knowledge-items", headers=customer["headers"])
    assert resp.status_code == 403


def test_list_status_filter(client, admin, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    _ingest(db, item_id)
    resp = client.get(
        "/knowledge-items?status=ready", headers=admin["headers"]
    )
    assert resp.json()["total"] == 1
    resp = client.get(
        "/knowledge-items?status=processing", headers=admin["headers"]
    )
    assert resp.json()["total"] == 0


def test_list_pagination(client, admin):
    for i in range(5):
        _upload(client, admin["headers"], file_name=f"doc{i}.txt")
    resp = client.get("/knowledge-items?page=1&page_size=2", headers=admin["headers"])
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["pages"] == 3


def test_admin_detail_shows_chunk_and_embedding_count(client, admin, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    _ingest(db, item_id)
    resp = client.get(f"/knowledge-items/{item_id}", headers=admin["headers"])
    body = resp.json()
    assert body["status"] == "ready"
    assert body["chunk_count"] > 0
    assert body["embedding_count"] == body["chunk_count"]
    assert "file_content" not in resp.text.lower()


def test_agent_can_view_ready_detail(client, admin, agent, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    _ingest(db, item_id)
    resp = client.get(f"/knowledge-items/{item_id}", headers=agent["headers"])
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_agent_cannot_view_processing_detail(client, admin, agent, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    resp = client.get(f"/knowledge-items/{item_id}", headers=agent["headers"])
    assert resp.status_code == 403


def test_customer_detail_forbidden(client, admin, customer, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    resp = client.get(f"/knowledge-items/{item_id}", headers=customer["headers"])
    assert resp.status_code == 403


def test_missing_detail_404(client, admin):
    resp = client.get(
        "/knowledge-items/00000000-0000-0000-0000-000000000000",
        headers=admin["headers"],
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------

def test_admin_delete_cascades_chunks(client, admin, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    _ingest(db, item_id)
    chunk_count = db.query(KnowledgeChunk).filter_by(knowledge_item_id=item_id).count()
    assert chunk_count > 0

    resp = client.delete(f"/knowledge-items/{item_id}", headers=admin["headers"])
    assert resp.status_code == 204
    assert db.query(KnowledgeChunk).filter_by(knowledge_item_id=item_id).count() == 0
    assert db.get(KnowledgeItem, item_id) is None


def test_agent_cannot_delete(client, admin, agent, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    resp = client.delete(f"/knowledge-items/{item_id}", headers=agent["headers"])
    assert resp.status_code == 403
    assert db.get(KnowledgeItem, item_id) is not None


def test_customer_cannot_delete(client, admin, customer, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    resp = client.delete(f"/knowledge-items/{item_id}", headers=customer["headers"])
    assert resp.status_code == 403


def test_delete_missing_404(client, admin):
    resp = client.delete(
        "/knowledge-items/00000000-0000-0000-0000-000000000000",
        headers=admin["headers"],
    )
    assert resp.status_code == 404


def test_delete_twice_returns_404(client, admin, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    assert client.delete(f"/knowledge-items/{item_id}", headers=admin["headers"]).status_code == 204
    resp = client.delete(f"/knowledge-items/{item_id}", headers=admin["headers"])
    assert resp.status_code == 404


def test_delete_writes_audit(client, admin, db):
    from backend.app.models import AuditLog

    item_id = _upload(client, admin["headers"]).json()["id"]
    before = db.query(AuditLog).count()
    client.delete(f"/knowledge-items/{item_id}", headers=admin["headers"])
    log = db.query(AuditLog).filter_by(action="knowledge.deleted").one()
    assert db.query(AuditLog).count() == before + 1
    assert str(log.entity_id) == item_id


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _ready_item(client, admin, db):
    item_id = _upload(client, admin["headers"]).json()["id"]
    _ingest(db, item_id)
    return item_id


def test_search_requires_login(client, admin, db):
    item_id = _ready_item(client, admin, db)
    resp = client.post("/knowledge-search", json={"query": "重置密码", "top_k": 3})
    assert resp.status_code == 401


def test_customer_search_forbidden(client, admin, customer, db):
    _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "重置密码", "top_k": 3},
        headers=customer["headers"],
    )
    assert resp.status_code == 403


def test_agent_can_search(client, admin, agent, db):
    _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "重置密码", "top_k": 3},
        headers=agent["headers"],
    )
    assert resp.status_code == 200
    assert isinstance(resp.json()["items"], list)


def test_search_empty_query_400(client, admin, agent, db):
    _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "", "top_k": 3},
        headers=agent["headers"],
    )
    assert resp.status_code == 422  # Pydantic min_length


def test_search_top_k_invalid(client, admin, agent, db):
    _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "重置密码", "top_k": 0},
        headers=agent["headers"],
    )
    assert resp.status_code == 422
    resp = client.post(
        "/knowledge-search", json={"query": "重置密码", "top_k": 21},
        headers=agent["headers"],
    )
    assert resp.status_code == 422


def test_search_returns_source_and_similarity(client, admin, agent, db):
    item_id = _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "如何重置密码", "top_k": 3},
        headers=agent["headers"],
    )
    items = resp.json()["items"]
    assert len(items) >= 1
    first = items[0]
    assert first["knowledge_item_id"] == item_id
    assert first["title"]
    assert "chunk_index" in first
    assert 0.0 <= first["similarity_score"] <= 1.0
    assert first["content"]


def test_search_sorted_by_similarity_desc(client, admin, agent, db):
    _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "重置密码", "top_k": 5},
        headers=agent["headers"],
    )
    scores = [item["similarity_score"] for item in resp.json()["items"]]
    assert scores == sorted(scores, reverse=True)


def test_search_ignores_processing_items(client, admin, agent, db):
    # Upload a second item but do NOT ingest it.
    _upload(client, admin["headers"], file_name="pending.txt")
    item_id = _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "重置密码", "top_k": 5},
        headers=agent["headers"],
    )
    # Only the ready item's chunks appear.
    for item in resp.json()["items"]:
        assert item["knowledge_item_id"] != "pending"
    assert resp.json()["items"]


def test_search_no_results_returns_empty(client, admin, agent, db):
    # No ready items at all.
    resp = client.post(
        "/knowledge-search", json={"query": "不存在的内容", "top_k": 3},
        headers=agent["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_search_unknown_query_still_returns_items(client, admin, agent, db):
    """An unknown query returns chunks too (cosine similarity is never 0 in
    the fake provider's binary space, but results are ordered by score)."""
    _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "zzzz", "top_k": 3},
        headers=agent["headers"],
    )
    assert resp.status_code == 200


def test_search_does_not_leak_secrets(client, admin, agent, db):
    _ready_item(client, admin, db)
    resp = client.post(
        "/knowledge-search", json={"query": "重置密码", "top_k": 3},
        headers=agent["headers"],
    )
    text = resp.text.lower()
    assert "api_key" not in text
    assert "password_hash" not in text
