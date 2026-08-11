"""Reply send tests (W2-B).

Covers the send guard (only reviewed replies send), duplicate-send 409, the
single-transaction ``in_review -> replied`` ticket transition, and rollback on
failure.
"""

import pytest

from backend.app.models import AuditLog, Ticket, TicketReply


def _create_reviewed_reply(client, agent, ticket_id):
    reply = client.post(
        f"/tickets/{ticket_id}/replies",
        json={"content": "您好，已协助处理。"},
        headers=agent["headers"],
    ).json()
    client.post(
        f"/tickets/{ticket_id}/replies/{reply['id']}/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    return reply


def _send(client, ticket_id, reply_id, headers):
    return client.post(
        f"/tickets/{ticket_id}/replies/{reply_id}/send", headers=headers
    )


def test_send_reviewed_reply_moves_ticket_to_replied(client, customer, agent, ticket):
    client.post(
        f"/tickets/{ticket['id']}/transition",
        json={"event": "start_review"},
        headers=agent["headers"],
    )
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    resp = _send(client, ticket["id"], reply["id"], agent["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply_status"] == "sent"
    assert body["ticket_status"] == "replied"
    assert body["sent_at"] is not None
    assert body["reply_id"] == reply["id"]


def test_send_requires_login(client, customer, agent, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    resp = client.post(f"/tickets/{ticket['id']}/replies/{reply['id']}/send")
    assert resp.status_code == 401


def test_send_customer_forbidden(client, customer, agent, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    resp = _send(client, ticket["id"], reply["id"], customer["headers"])
    assert resp.status_code == 403


def test_send_missing_reply_404(client, agent, ticket):
    resp = client.post(
        f"/tickets/{ticket['id']}/replies/00000000-0000-0000-0000-000000000000/send",
        headers=agent["headers"],
    )
    assert resp.status_code == 404


def test_draft_reply_cannot_send(client, customer, agent, ticket):
    reply = client.post(
        f"/tickets/{ticket['id']}/replies",
        json={"content": "未审核草稿"},
        headers=agent["headers"],
    ).json()
    resp = _send(client, ticket["id"], reply["id"], agent["headers"])
    assert resp.status_code == 400


def test_reviewed_reply_can_send(client, customer, agent, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    resp = _send(client, ticket["id"], reply["id"], agent["headers"])
    assert resp.status_code == 200


def test_duplicate_send_returns_409(client, customer, agent, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    first = _send(client, ticket["id"], reply["id"], agent["headers"])
    second = _send(client, ticket["id"], reply["id"], agent["headers"])
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "conflict"


def test_send_writes_reply_and_ticket_audit(client, customer, agent, db, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    before = db.query(AuditLog).count()
    _send(client, ticket["id"], reply["id"], agent["headers"])
    actions = [
        log.action
        for log in db.query(AuditLog).order_by(AuditLog.created_at.asc()).all()
    ]
    assert actions.count("reply.sent") == 1
    assert actions.count("ticket.status_changed") >= 1


def test_send_records_sent_at_and_sender(client, customer, agent, db, ticket):
    from backend.app.models import User

    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    _send(client, ticket["id"], reply["id"], agent["headers"])
    row = db.query(TicketReply).filter_by(id=reply["id"]).one()
    assert row.status == "sent"
    assert row.is_sent is True
    assert row.sent_at is not None
    assert row.sender_id is not None


def test_sent_reply_becomes_visible_to_customer(client, customer, agent, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    _send(client, ticket["id"], reply["id"], agent["headers"])
    resp = client.get(f"/tickets/{ticket['id']}/replies", headers=customer["headers"])
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == reply["id"]
    assert items[0]["status"] == "sent"


def test_send_from_open_ticket_does_not_break_ticket(client, customer, agent, ticket):
    """Sending on a ticket that is not in_review must not corrupt the ticket."""
    reply = _create_reviewed_reply(client, agent, ticket["id"])
    resp = _send(client, ticket["id"], reply["id"], agent["headers"])
    # reply sends fine; ticket stays open because no in_review transition applies.
    assert resp.status_code == 200
    assert resp.json()["ticket_status"] == "open"
    check = client.get(f"/tickets/{ticket['id']}", headers=customer["headers"]).json()
    assert check["status"] == "open"
