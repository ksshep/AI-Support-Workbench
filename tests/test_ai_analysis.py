"""AI analysis API tests (W3-A).

Exercises ``GET /tickets/{id}/ai-analysis`` end-to-end: job creation on
ticket create, permission scoping (401/403/404), the status flow, and the
trigger endpoint's idempotency.
"""

import pytest

from backend.app.models import AIProcessingJob
from backend.app.services.ai_analysis_service import create_analysis_job


def test_ai_analysis_requires_login(client, customer):
    created = client.post(
        "/tickets",
        json={"title": "无法登录", "description": "密码正确但无法登录"},
        headers=customer["headers"],
    ).json()
    resp = client.get(f"/tickets/{created['id']}/ai-analysis")
    assert resp.status_code == 401


def test_creating_ticket_produces_pending_job(client, customer, db, ticket):
    """创建工单后自动产生一个 pending 的 AIProcessingJob。"""
    from backend.app.models import AIProcessingJob

    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket["id"]).one()
    assert job.job_type == "ticket_analysis"
    assert job.status == "pending"
    assert job.attempts == 0


def test_get_ai_analysis_returns_job_shape(client, customer, ticket):
    resp = client.get(f"/tickets/{ticket['id']}/ai-analysis", headers=customer["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket_id"] == ticket["id"]
    assert body["job_type"] == "ticket_analysis"
    assert body["status"] == "pending"
    assert body["retry_count"] == 0
    assert body["error_message"] is None
    assert body["job_id"]
    assert body["created_at"]
    assert body["updated_at"]
    assert "result" in body


def test_other_customer_cannot_view_analysis(client, customer, other_customer, ticket):
    resp = client.get(
        f"/tickets/{ticket['id']}/ai-analysis", headers=other_customer["headers"]
    )
    assert resp.status_code == 403


def test_agent_can_view_any_analysis(client, customer, agent, ticket):
    resp = client.get(
        f"/tickets/{ticket['id']}/ai-analysis", headers=agent["headers"]
    )
    assert resp.status_code == 200
    assert resp.json()["ticket_id"] == ticket["id"]


def test_admin_can_view_any_analysis(client, customer, admin, ticket):
    resp = client.get(
        f"/tickets/{ticket['id']}/ai-analysis", headers=admin["headers"]
    )
    assert resp.status_code == 200


def test_missing_ticket_404(client, customer):
    resp = client.get(
        "/tickets/00000000-0000-0000-0000-000000000000/ai-analysis",
        headers=customer["headers"],
    )
    assert resp.status_code == 404


def test_missing_job_404(client, customer, other_customer, agent, db):
    """A ticket that exists but never got an analysis job returns 404."""
    created = client.post(
        "/tickets",
        json={"title": "无分析任务", "description": "描述"},
        headers=other_customer["headers"],
    ).json()
    # Remove the auto-created job so the endpoint genuinely has none.
    job = db.query(AIProcessingJob).filter_by(ticket_id=created["id"]).one()
    db.delete(job)
    db.commit()
    resp = client.get(
        f"/tickets/{created['id']}/ai-analysis", headers=agent["headers"]
    )
    assert resp.status_code == 404


def test_create_ticket_does_not_block_on_queue(client, customer, fake_redis):
    """创建工单接口必须立即返回 201，不能等待 worker。"""
    import time

    start = time.monotonic()
    resp = client.post(
        "/tickets",
        json={"title": "快速返回", "description": "接口不应等待 AI"},
        headers=customer["headers"],
    )
    elapsed = time.monotonic() - start
    assert resp.status_code == 201
    # Creating a ticket must not wait seconds on a worker.
    assert elapsed < 2.0


def test_create_ticket_response_still_open(client, customer, ticket):
    """创建后工单仍是 open，AI 分析异步进行。"""
    resp = client.get(f"/tickets/{ticket['id']}", headers=customer["headers"])
    assert resp.json()["status"] == "open"
    assert resp.json()["classification"] == "technical"
    assert resp.json()["summary"] == ""


def test_retry_count_reflects_db_attempts(client, customer, db, ticket):
    """直接驱动任务失败后，查询接口返回 retry_count。"""
    from backend.app.tasks.ticket_analysis import run_ticket_analysis

    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket["id"]).one()
    provider = _failing_provider()
    _run_with_provider(job.id, provider)
    db.expire_all()
    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket["id"]).one()

    resp = client.get(
        f"/tickets/{ticket['id']}/ai-analysis", headers=customer["headers"]
    )
    body = resp.json()
    assert body["retry_count"] == job.attempts
    assert body["error_message"]


def test_task_success_updates_query_result(client, customer, db, ticket):
    """成功后 status 变 succeeded，result 出现在查询接口里。"""
    from backend.app.chat_provider import FakeChatProvider
    from backend.app.tasks.ticket_analysis import run_ticket_analysis

    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket["id"]).one()
    _run_with_provider(job.id, FakeChatProvider())

    resp = client.get(
        f"/tickets/{ticket['id']}/ai-analysis", headers=customer["headers"]
    )
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["result"]["priority"] == "high"
    assert body["result"]["category"] == "technical"
    assert body["result"]["confidence"] == pytest.approx(0.91)


def test_trigger_by_customer_forbidden(client, customer, ticket):
    resp = client.post(
        f"/tickets/{ticket['id']}/ai-analysis/trigger", headers=customer["headers"]
    )
    assert resp.status_code == 403


def test_trigger_creates_job_when_missing(client, other_customer, agent, db):
    created = client.post(
        "/tickets",
        json={"title": "待分析", "description": "还没有任务"},
        headers=other_customer["headers"],
    ).json()
    # Delete the auto-created job so the trigger path creates a fresh one.
    job = db.query(AIProcessingJob).filter_by(ticket_id=created["id"]).one()
    db.delete(job)
    db.commit()

    resp = client.post(
        f"/tickets/{created['id']}/ai-analysis/trigger", headers=agent["headers"]
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["job_type"] == "ticket_analysis"


def test_trigger_returns_existing_pending_job(client, customer, agent, ticket, db):
    """同一工单不能创建第二个未完成的分析任务。"""
    resp1 = client.post(
        f"/tickets/{ticket['id']}/ai-analysis/trigger", headers=agent["headers"]
    )
    resp2 = client.post(
        f"/tickets/{ticket['id']}/ai-analysis/trigger", headers=agent["headers"]
    )
    assert resp1.json()["job_id"] == resp2.json()["job_id"]
    count = db.query(AIProcessingJob).filter_by(
        ticket_id=ticket["id"], job_type="ticket_analysis"
    ).count()
    assert count == 1


def test_no_duplicate_unfinished_job_for_ticket(client, customer, agent, db, ticket):
    """创建工单 + 多次 trigger 后，ticket 仍然只有一条任务记录。"""
    for _ in range(3):
        client.post(
            f"/tickets/{ticket['id']}/ai-analysis/trigger", headers=agent["headers"]
        )
    count = db.query(AIProcessingJob).filter_by(
        ticket_id=ticket["id"], job_type="ticket_analysis"
    ).count()
    assert count == 1


def test_trigger_reruns_failed_job(client, customer, agent, db, ticket):
    """失败的任务可以通过 trigger 重新置为 pending 并重试。"""
    from backend.app.tasks.ticket_analysis import run_ticket_analysis

    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket["id"]).one()
    _run_with_provider(job.id, _failing_provider())
    db.expire_all()
    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket["id"]).one()
    assert job.status == "processing"

    # Mark failed to simulate RQ exhausting retries.
    job.status = "failed"
    job.error_message = "超出重试次数"
    db.commit()

    resp = client.post(
        f"/tickets/{ticket['id']}/ai-analysis/trigger", headers=agent["headers"]
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "pending"
    assert row.error_message is None


def test_trigger_does_not_rerun_succeeded_job(client, customer, agent, db, ticket):
    from backend.app.chat_provider import FakeChatProvider
    from backend.app.tasks.ticket_analysis import run_ticket_analysis

    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket["id"]).one()
    _run_with_provider(job.id, FakeChatProvider())

    resp = client.post(
        f"/tickets/{ticket['id']}/ai-analysis/trigger", headers=agent["headers"]
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "succeeded"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failing_provider():
    from backend.app.chat_provider import FakeChatProvider, StructuredOutputError

    return FakeChatProvider(
        fail_with=StructuredOutputError("模型输出无法解析"), failures_remaining=10**6
    )


def _run_with_provider(job_id, provider):
    import backend.app.tasks.ticket_analysis as task_mod
    from backend.app import provider_factory

    original = task_mod.get_chat_provider
    task_mod.get_chat_provider = lambda: provider
    provider_factory._chat_provider_cache = provider
    try:
        from backend.app.tasks.ticket_analysis import run_ticket_analysis

        try:
            return run_ticket_analysis(str(job_id))
        except Exception:
            return None
    finally:
        task_mod.get_chat_provider = original
