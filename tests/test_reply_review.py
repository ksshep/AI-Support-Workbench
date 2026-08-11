"""Reply review tests (W2-B).

Covers approve (draft -> reviewed), reject (stays draft with decision
recorded), and the guards: staff-only, sent replies are terminal, and review
never auto-sends.
"""

import pytest

from backend.app.models import AuditLog, TicketReply


def _create_reply(client, agent, ticket_id):
    return client.post(
        f"/tickets/{ticket_id}/replies",
        json={"content": "您好，我们已收到您的问题。"},
        headers=agent["headers"],
    ).json()


def _review(client, ticket_id, reply_id, headers, approved=True):
    return client.post(
        f"/tickets/{ticket_id}/replies/{reply_id}/review",
        json={"approved": approved},
        headers=headers,
    )


def test_agent_approves_draft(client, customer, agent, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    resp = _review(client, ticket["id"], reply["id"], agent["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "reviewed"
    assert body["approved"] is True
    assert body["reviewer_id"] is not None
    assert body["reviewed_at"] is not None


def test_admin_approves_draft(client, customer, admin, ticket):
    reply = _create_reply(client, admin, ticket["id"])
    resp = _review(client, ticket["id"], reply["id"], admin["headers"])
    assert resp.status_code == 200
    assert resp.json()["status"] == "reviewed"


def test_customer_review_forbidden(client, customer, agent, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    resp = _review(client, ticket["id"], reply["id"], customer["headers"])
    assert resp.status_code == 403


def test_review_requires_login(client, agent, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    resp = client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/review",
        json={"approved": True},
    )
    assert resp.status_code == 401


def test_review_missing_reply_404(client, agent, ticket):
    resp = client.post(
        f"/tickets/{ticket['id']}/replies/00000000-0000-0000-0000-000000000000/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    assert resp.status_code == 404


def test_review_wrong_ticket_404(client, customer, agent, ticket):
    """A reply that belongs to a different ticket is treated as not found."""
    reply = _create_reply(client, agent, ticket["id"])
    other = client.post(
        "/tickets",
        json={"title": "另一张单", "description": "描述"},
        headers=customer["headers"],
    ).json()
    resp = client.post(
        f"/tickets/{other['id']}/replies/{reply['id']}/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    assert resp.status_code == 404


def test_sent_reply_cannot_be_reviewed(client, customer, agent, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    _review(client, ticket["id"], reply["id"], agent["headers"])
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/send",
        headers=agent["headers"],
    )
    resp = _review(client, ticket["id"], reply["id"], agent["headers"])
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "conflict"


def test_reject_keeps_draft_and_records_decision(client, customer, agent, db, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    resp = _review(client, ticket["id"], reply["id"], agent["headers"], approved=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert resp.json()["approved"] is False
    # Review decision persisted.
    row = db.query(TicketReply).filter_by(id=reply["id"]).one()
    assert row.status == "draft"
    assert row.reviewed_at is None  # rejection does not stamp reviewed_at


def test_review_writes_audit(client, customer, agent, db, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    before = db.query(AuditLog).count()
    _review(client, ticket["id"], reply["id"], agent["headers"])
    assert db.query(AuditLog).count() == before + 1
    log = db.query(AuditLog).filter_by(action="reply.approved").one()
    assert log.actor_id is not None
    assert str(log.entity_id) == ticket["id"]
    assert log.new_value["status"] == "reviewed"


def test_reject_writes_audit(client, customer, agent, db, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    _review(client, ticket["id"], reply["id"], agent["headers"], approved=False)
    log = db.query(AuditLog).filter_by(action="reply.rejected").one()
    assert log.new_value["approved"] is False
    assert log.new_value["status"] == "draft"


def test_review_does_not_auto_send(client, customer, agent, ticket):
    reply = _create_reply(client, agent, ticket["id"])
    _review(client, ticket["id"], reply["id"], agent["headers"])
    row = (
        client.get(f"/tickets/{ticket['id']}/replies", headers=agent["headers"])
        .json()["items"]
    )
    sent = [r for r in row if r["id"] == reply["id"]]
    assert sent[0]["status"] == "reviewed"


def test_reviewer_is_actor(client, customer, agent, db, ticket):
    from backend.app.models import User

    reply = _create_reply(client, agent, ticket["id"])
    _review(client, ticket["id"], reply["id"], agent["headers"])
    row = db.query(TicketReply).filter_by(id=reply["id"]).one()
    agent_user = db.query(User).filter_by(email=agent["email"]).one()
    assert row.reviewer_id == agent_user.id
    assert row.reviewed_at is not None
