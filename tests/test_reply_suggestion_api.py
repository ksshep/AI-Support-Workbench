"""Reply-suggestion API tests (W4-A).

End-to-end HTTP tests over the trigger + query endpoints, the permission
model, the one-job-per-ticket idempotency (409), and the reuse of the
existing review/send endpoints to move an AI draft through the lifecycle.
"""

import pytest

from backend.app.models import AIProcessingJob, Ticket, TicketReply


def _ready_knowledge(client, admin):
    """Upload + ingest a knowledge item so RAG has sources."""
    from tests.fixtures import sample_txt_bytes

    resp = client.post(
        "/knowledge-items",
        files={"file": ("faq.txt", sample_txt_bytes())},
        headers=admin["headers"],
    )
    assert resp.status_code == 201
    item_id = resp.json()["id"]
    from backend.app.tasks.knowledge_ingestion import run_knowledge_ingestion

    run_knowledge_ingestion(item_id)
    return item_id


def _ticket_in_review(client, agent, ticket_id):
    resp = client.post(
        f"/tickets/{ticket_id}/transition",
        json={"event": "start_review"},
        headers=agent["headers"],
    )
    assert resp.status_code == 200
    return resp.json()


def _run_job(db, job_id):
    """Run the reply-suggestion task body directly with fake providers."""
    import backend.app.tasks.reply_suggestion as task_mod
    from backend.app import provider_factory
    from backend.app.chat_provider import FakeChatProvider
    from backend.app.embedding import FakeEmbeddingProvider

    provider = FakeChatProvider()
    original_chat = task_mod.get_chat_provider
    original_emb = task_mod.get_embedding_provider
    task_mod.get_chat_provider = lambda: provider
    task_mod.get_embedding_provider = lambda: FakeEmbeddingProvider()
    provider_factory._chat_provider_cache = provider
    provider_factory._embedding_provider_cache = FakeEmbeddingProvider()
    try:
        return task_mod.run_reply_suggestion(str(job_id))
    except Exception:
        return None
    finally:
        task_mod.get_chat_provider = original_chat
        task_mod.get_embedding_provider = original_emb


def _customer_headers(client, ticket):
    """Bearer token for the ticket's owning customer."""
    from backend.app.database import SessionLocal
    from backend.app.security import create_access_token

    db = SessionLocal()
    try:
        owner = db.get(Ticket, ticket["id"]).customer
        token = create_access_token(owner.id, owner.role)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Trigger: permissions and ticket state
# --------------------------------------------------------------------------

def test_agent_triggers_suggestion(client, admin, agent, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    resp = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ticket_id"] == ticket["id"]
    assert body["job_type"] == "reply_suggestion"
    assert body["status"] == "pending"
    assert body["job_id"]


def test_admin_triggers_suggestion(client, admin, agent, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    resp = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=admin["headers"]
    )
    assert resp.status_code == 201


def test_customer_cannot_trigger(client, admin, customer, agent, ticket):
    _ticket_in_review(client, agent, ticket["id"])
    resp = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=customer["headers"]
    )
    assert resp.status_code == 403


def test_trigger_requires_login(client, admin, agent, ticket):
    _ticket_in_review(client, agent, ticket["id"])
    resp = client.post(f"/tickets/{ticket['id']}/reply-suggestions")
    assert resp.status_code == 401


def test_trigger_missing_ticket_404(client, agent):
    resp = client.post(
        "/tickets/00000000-0000-0000-0000-000000000000/reply-suggestions",
        headers=agent["headers"],
    )
    assert resp.status_code == 404


def test_trigger_only_in_review_ticket(client, admin, agent, ticket):
    # Ticket is open (not in_review) -> 400.
    resp = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    assert resp.status_code == 400


def test_trigger_replied_ticket_400(client, admin, agent, ticket):
    """A replied ticket (already sent) cannot trigger a suggestion."""
    from backend.app.database import SessionLocal
    from backend.app.services.state_machine import next_status

    _ticket_in_review(client, agent, ticket["id"])
    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket["id"])
        t.status = next_status(t.status, "reply")
        db.commit()
    finally:
        db.close()
    resp = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Trigger -> pending -> succeeded flow
# --------------------------------------------------------------------------

def test_trigger_returns_quickly_pending(client, admin, agent, ticket, fake_redis):
    """触发后接口必须快速返回 pending，不能等待 worker。"""
    import time

    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    start = time.monotonic()
    resp = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    elapsed = time.monotonic() - start
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"
    assert elapsed < 2.0


def test_job_flow_to_succeeded_creates_draft(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]

    row = db.get(AIProcessingJob, job_id)
    assert row.status == "pending"

    _run_job(db, job_id)
    db.expire_all()
    row = db.get(AIProcessingJob, job_id)
    assert row.status == "succeeded"
    assert row.result and row.result["reply_id"]

    draft = db.get(TicketReply, row.result["reply_id"])
    assert draft.is_ai_suggestion is True
    assert draft.status == "draft"


def test_get_suggestion_agent_sees_full_state(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]
    _run_job(db, job_id)

    resp = client.get(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["reply_id"]
    assert body["source_refs"]
    assert body["retry_count"] >= 0
    assert "ticket_id" in body


def test_get_suggestion_customer_sees_safe_status(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]
    _run_job(db, job_id)

    resp = client.get(
        f"/tickets/{ticket['id']}/reply-suggestions",
        headers=_customer_headers(client, ticket),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["job_id"] == job_id
    # Safe fields only.
    assert "error_message" not in body
    assert "source_refs" not in body
    assert "reply_id" not in body
    assert "retry_count" not in body


def test_get_suggestion_other_customer_403(client, admin, other_customer, agent, ticket):
    _ticket_in_review(client, agent, ticket["id"])
    client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    resp = client.get(
        f"/tickets/{ticket['id']}/reply-suggestions",
        headers=other_customer["headers"],
    )
    assert resp.status_code == 403


def test_get_suggestion_missing_job_404(client, admin, agent, ticket):
    _ticket_in_review(client, agent, ticket["id"])
    resp = client.get(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Idempotency: one job per ticket
# --------------------------------------------------------------------------

def test_duplicate_trigger_409(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    first = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    assert first.status_code == 201
    second = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    assert second.status_code == 409
    assert db.query(AIProcessingJob).filter_by(
        ticket_id=ticket["id"], job_type="reply_suggestion"
    ).count() == 1


def test_duplicate_after_succeeded_409(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]
    _run_job(db, job_id)
    resp = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------
# Draft lifecycle through existing review/send endpoints
# --------------------------------------------------------------------------

def test_ai_draft_visible_to_agent(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]
    _run_job(db, job_id)

    resp = client.get(f"/tickets/{ticket['id']}/replies", headers=agent["headers"])
    drafts = [r for r in resp.json()["items"] if r["is_ai_suggestion"]]
    assert len(drafts) == 1
    assert drafts[0]["status"] == "draft"
    assert drafts[0]["content"]


def test_ai_draft_invisible_to_customer(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]
    _run_job(db, job_id)

    resp = client.get(
        f"/tickets/{ticket['id']}/replies", headers=_customer_headers(client, ticket)
    )
    items = resp.json()["items"]
    assert all(r["status"] == "sent" for r in items)
    assert not any(r["is_ai_suggestion"] for r in items)


def test_ai_draft_review_then_send_full_cycle(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]
    _run_job(db, job_id)

    db.expire_all()
    job = db.get(AIProcessingJob, job_id)
    draft = db.get(TicketReply, job.result["reply_id"])

    # Review (draft -> reviewed).
    resp = client.post(
        f"/tickets/{ticket['id']}/replies/{draft.id}/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reviewed"

    # Send (reviewed -> sent, ticket in_review -> replied).
    resp = client.post(
        f"/tickets/{ticket['id']}/replies/{draft.id}/send",
        headers=agent["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["reply_status"] == "sent"
    assert resp.json()["ticket_status"] == "replied"

    # Customer now sees the sent reply.
    resp = client.get(
        f"/tickets/{ticket['id']}/replies", headers=_customer_headers(client, ticket)
    )
    sent = [r for r in resp.json()["items"] if r["status"] == "sent"]
    assert any(r["id"] == str(draft.id) for r in sent)


def test_ai_draft_cannot_send_without_review(client, admin, agent, db, ticket):
    _ready_knowledge(client, admin)
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]
    _run_job(db, job_id)
    db.expire_all()
    draft = db.get(TicketReply, db.get(AIProcessingJob, job_id).result["reply_id"])

    resp = client.post(
        f"/tickets/{ticket['id']}/replies/{draft.id}/send", headers=agent["headers"]
    )
    assert resp.status_code == 400  # only reviewed replies can be sent


def test_failure_no_draft_and_safe_error(client, admin, agent, db, ticket):
    _ticket_in_review(client, agent, ticket["id"])
    job_id = client.post(
        f"/tickets/{ticket['id']}/reply-suggestions", headers=agent["headers"]
    ).json()["job_id"]

    # Force a failure: no knowledge + provider claiming a source is rejected.
    import backend.app.tasks.reply_suggestion as task_mod
    from backend.app import provider_factory
    from backend.app.chat_provider import FakeChatProvider

    provider = FakeChatProvider(
        raw_output=(
            '{"reply": "根据知识库说明，请检查网络。", "confidence": 0.8, '
            '"should_escalate": true, "reason": "有依据"}'
        )
    )
    original = task_mod.get_chat_provider
    task_mod.get_chat_provider = lambda: provider
    provider_factory._chat_provider_cache = provider
    try:
        task_mod.run_reply_suggestion(job_id)
    except Exception:
        pass
    finally:
        task_mod.get_chat_provider = original

    db.expire_all()
    row = db.get(AIProcessingJob, job_id)
    assert row.status == "failed"
    assert "知识库" in row.error_message
    assert db.query(TicketReply).filter_by(is_ai_suggestion=True).count() == 0
