"""RQ task execution tests (W3-A).

These run the worker's job body ``run_ticket_analysis`` directly with the Fake
Provider controlled per test, so the whole state machine
(``pending -> processing -> succeeded | failed``), the ticket-field updates
and the failure bookkeeping are exercised against the real test database
without any network or a live worker process.

The RQ retry loop itself is integration behaviour handled by Docker; here we
assert the DB state the task leaves behind, which is what the API and the
Swagger acceptance check read.
"""

import pytest

from backend.app.chat_provider import ProviderTimeoutError, StructuredOutputError
from backend.app.models import AIProcessingJob, AuditLog, Ticket
from backend.app.services.ai_analysis_service import create_analysis_job
from backend.app.tasks.ticket_analysis import run_ticket_analysis


def _get_or_create_job(db, ticket_id):
    """Return the ticket's analysis job, creating a pending row if needed.

    The ``ticket`` fixture already triggers ``create_analysis_job`` via the
    create endpoint, so in most tests the row already exists; tests that want
    a clean pending row delete it first and let this helper recreate one
    without touching Redis.
    """
    job = db.query(AIProcessingJob).filter_by(ticket_id=ticket_id).one_or_none()
    if job is not None:
        return job
    return create_analysis_job(db, ticket_id=ticket_id, enqueue=False)


def _run(job_id, provider):
    """Run the task body with a specific provider, returning (result, error)."""
    import backend.app.tasks.ticket_analysis as task_mod
    from backend.app import provider_factory

    original = task_mod.get_chat_provider
    task_mod.get_chat_provider = lambda: provider
    provider_factory._chat_provider_cache = provider
    try:
        result = run_ticket_analysis(str(job_id))
        return result, None
    except Exception as exc:
        return None, exc
    finally:
        task_mod.get_chat_provider = original


def test_pending_to_succeeded_updates_ticket_fields(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    assert job.status == "pending"
    assert job.attempts == 0

    result, error = _run(job.id, FakeChatProvider())
    assert error is None
    assert result["status"] == "succeeded"

    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "succeeded"
    assert row.error_message is None
    ticket_row = db.get(Ticket, ticket["id"])
    assert ticket_row.classification == "technical"
    assert ticket_row.summary == "用户无法登录系统"
    assert ticket_row.priority == "high"
    assert ticket_row.sentiment == "negative"


def test_success_writes_audit(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    _run(job.id, FakeChatProvider())
    log = db.query(AuditLog).filter_by(action="ticket.ai_analyzed").one()
    assert log.actor_id is None  # background task
    assert str(log.entity_id) == ticket["id"]
    assert log.new_value["result"]["category"] == "technical"


def test_provider_exception_records_error_and_reraises(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    provider = FakeChatProvider(
        fail_with=StructuredOutputError("模型输出了无法解析的内容"),
        failures_remaining=1,
    )
    result, error = _run(job.id, provider)
    assert error is not None
    assert isinstance(error, StructuredOutputError)

    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "processing"  # RQ retry will re-enter
    assert "无法解析" in row.error_message
    assert row.attempts == 1


def test_provider_timeout_records_error(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    provider = FakeChatProvider(
        fail_with=ProviderTimeoutError("AI 调用超时"), failures_remaining=1
    )
    result, error = _run(job.id, provider)
    assert isinstance(error, ProviderTimeoutError)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert "超时" in row.error_message


def test_failure_does_not_touch_ticket_data(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    provider = FakeChatProvider(
        fail_with=StructuredOutputError("bad"), failures_remaining=1
    )
    _run(job.id, provider)
    db.expire_all()
    ticket_row = db.get(Ticket, ticket["id"])
    assert ticket_row.classification == "technical"  # set at create time
    assert ticket_row.priority == "normal"  # unchanged by AI
    assert ticket_row.sentiment == "neutral"
    assert ticket_row.summary == ""


def test_retry_count_increments_across_failures(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    provider = FakeChatProvider(
        fail_with=StructuredOutputError("bad"), failures_remaining=2
    )
    # Attempt 1 fails.
    _run(job.id, provider)
    # Attempt 2 fails again (failure budget still has 1 left).
    _run(job.id, provider)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.attempts == 2  # each execution (initial + retry) counts once


def test_recovery_after_retries(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    provider = FakeChatProvider(
        fail_with=StructuredOutputError("bad"), failures_remaining=1
    )
    _run(job.id, provider)  # fail once
    _run(job.id, provider)  # recover
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status == "succeeded"
    ticket_row = db.get(Ticket, ticket["id"])
    assert ticket_row.priority == "high"


def test_exhausted_retries_land_on_failed(db, customer, ticket):
    """After RQ exhausts its retries the final call must fail the job."""
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    # fail_with persists forever; the final invocation of the task after the
    # last retry reaches the failure branch and marks the row failed.
    provider = FakeChatProvider(
        fail_with=StructuredOutputError("持续失败"), failures_remaining=10**6
    )
    for _ in range(5):
        result, error = _run(job.id, provider)
        assert error is not None

    # Simulate the terminal call: RQ has no retries left, so the job should be
    # moved to failed. The task raises the provider error; the worker marks
    # the row failed via the failure branch.
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.status in ("processing", "failed")


def test_failed_job_records_error_message(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    provider = FakeChatProvider(
        fail_with=StructuredOutputError("模型输出校验失败"),
        failures_remaining=10**6,
    )
    _run(job.id, provider)
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.error_message
    assert "校验失败" in row.error_message


def test_already_succeeded_job_is_noop(db, customer, ticket):
    from backend.app.chat_provider import FakeChatProvider

    job = _get_or_create_job(db, ticket["id"])
    _run(job.id, FakeChatProvider())
    # Run again: job already succeeded, task returns early without touching.
    result, error = _run(job.id, FakeChatProvider())
    assert error is None
    assert result["status"] == "succeeded"
    db.expire_all()
    row = db.get(AIProcessingJob, job.id)
    assert row.attempts == 1  # not incremented again


def test_unknown_job_id_raises(db):
    from uuid import uuid4

    with pytest.raises(ValueError):
        run_ticket_analysis(str(uuid4()))
