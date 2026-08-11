"""Reply creation and listing tests (W2-B).

Covers role gates for creating replies, closed/canceled ticket rejection,
customer visibility of draft/reviewed/sent replies, and staff full visibility.
"""

import pytest

from backend.app.models import TicketReply

CONTENT = "您好，我们已收到您的问题，正在协助处理。"


def _create_reply(client, ticket_id, headers, content=CONTENT):
    return client.post(
        f"/tickets/{ticket_id}/replies",
        json={"content": content},
        headers=headers,
    )


@pytest.fixture
def in_review_ticket(client, customer, agent, ticket):
    """A ticket moved to in_review by the agent."""
    client.post(
        f"/tickets/{ticket['id']}/transition",
        json={"event": "start_review"},
        headers=agent["headers"],
    )
    return ticket


def test_agent_creates_draft(client, customer, agent, ticket):
    resp = _create_reply(client, ticket["id"], agent["headers"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert body["content"] == CONTENT
    assert "sender_id" in body
    assert body["is_ai_suggestion"] is False
    assert "sent_at" not in body


def test_admin_creates_draft(client, customer, admin, ticket):
    resp = _create_reply(client, ticket["id"], admin["headers"])
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"


def test_customer_creates_reply_forbidden(client, customer, ticket):
    resp = _create_reply(client, ticket["id"], customer["headers"])
    assert resp.status_code == 403


def test_create_reply_requires_login(client, ticket):
    resp = client.post(f"/tickets/{ticket['id']}/replies", json={"content": CONTENT})
    assert resp.status_code == 401


def test_create_reply_missing_ticket_404(client, agent):
    resp = client.post(
        "/tickets/00000000-0000-0000-0000-000000000000/replies",
        json={"content": CONTENT},
        headers=agent["headers"],
    )
    assert resp.status_code == 404


def test_closed_ticket_rejects_reply(client, customer, agent, db, ticket):
    # Move through the full lifecycle to closed.
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "reply"}, headers=agent["headers"])
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "close"}, headers=agent["headers"])
    resp = _create_reply(client, ticket["id"], agent["headers"])
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_state_transition"


def test_canceled_ticket_rejects_reply(client, customer, agent, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "cancel"}, headers=customer["headers"])
    resp = _create_reply(client, ticket["id"], agent["headers"])
    assert resp.status_code == 400


def test_create_reply_blank_content_rejected(client, agent, ticket):
    resp = _create_reply(client, ticket["id"], agent["headers"], content="   ")
    assert resp.status_code == 422


def test_create_reply_cannot_smuggle_fields(client, agent, ticket):
    """sender_id / status / reviewer_id / sent_at / is_ai_suggestion must be ignored."""
    forged = {
        "content": CONTENT,
        "sender_id": "00000000-0000-0000-0000-000000000000",
        "status": "sent",
        "reviewer_id": "00000000-0000-0000-0000-000000000000",
        "sent_at": "2026-01-01T00:00:00Z",
        "is_ai_suggestion": True,
    }
    resp = client.post(
        f"/tickets/{ticket['id']}/replies", json=forged, headers=agent["headers"]
    )
    assert resp.status_code == 422


def test_create_reply_sender_is_agent(client, agent, db, ticket):
    from backend.app.models import User

    resp = _create_reply(client, ticket["id"], agent["headers"])
    reply = db.query(TicketReply).filter_by(id=resp.json()["id"]).one()
    agent_user = db.query(User).filter_by(email=agent["email"]).one()
    assert reply.sender_id == agent_user.id
    assert reply.status == "draft"
    assert reply.is_sent is False


def test_create_reply_writes_audit_with_length_not_content(client, agent, db, ticket):
    from backend.app.models import AuditLog

    _create_reply(client, ticket["id"], agent["headers"])
    log = db.query(AuditLog).filter_by(action="reply.created").one()
    assert log.entity_type == "ticket"
    assert str(log.entity_id) == ticket["id"]
    assert log.new_value["content_length"] == len(CONTENT)
    # The audit must never store the full reply content.
    assert CONTENT not in str(log.new_value)


def test_list_replies_requires_login(client, ticket):
    assert client.get(f"/tickets/{ticket['id']}/replies").status_code == 401


def test_list_replies_missing_ticket_404(client, agent):
    resp = client.get(
        "/tickets/00000000-0000-0000-0000-000000000000/replies",
        headers=agent["headers"],
    )
    assert resp.status_code == 404


def test_customer_cannot_see_draft_reply(client, customer, agent, ticket):
    _create_reply(client, ticket["id"], agent["headers"])
    resp = client.get(f"/tickets/{ticket['id']}/replies", headers=customer["headers"])
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_customer_cannot_see_reviewed_reply(client, customer, agent, ticket):
    reply = _create_reply(client, ticket["id"], agent["headers"]).json()
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    resp = client.get(f"/tickets/{ticket['id']}/replies", headers=customer["headers"])
    assert resp.json()["items"] == []


def test_customer_can_see_sent_reply(client, customer, agent, ticket):
    reply = _create_reply(client, ticket["id"], agent["headers"]).json()
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/send",
        headers=agent["headers"],
    )
    resp = client.get(f"/tickets/{ticket['id']}/replies", headers=customer["headers"])
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["content"] == CONTENT
    assert items[0]["status"] == "sent"


def test_agent_sees_all_replies(client, customer, agent, ticket):
    reply = _create_reply(client, ticket["id"], agent["headers"]).json()
    # Review + send to reach sent, so all three states exist.
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/send",
        headers=agent["headers"],
    )
    _create_reply(client, ticket["id"], agent["headers"], content="第二封草稿")
    resp = client.get(f"/tickets/{ticket['id']}/replies", headers=agent["headers"])
    statuses = [item["status"] for item in resp.json()["items"]]
    assert "sent" in statuses
    assert "draft" in statuses
    assert len(statuses) == 2


def test_customer_cannot_list_other_ticket_replies(client, customer, other_customer, agent, ticket):
    _create_reply(client, ticket["id"], agent["headers"])
    resp = client.get(
        f"/tickets/{ticket['id']}/replies", headers=other_customer["headers"]
    )
    assert resp.status_code == 403


def test_replies_ordered_by_created_at(client, customer, agent, db, ticket):
    for i in range(3):
        client.post(
            f"/tickets/{ticket['id']}/replies",
            json={"content": f"第{i}条回复"},
            headers=agent["headers"],
        )
    resp = client.get(f"/tickets/{ticket['id']}/replies", headers=agent["headers"])
    contents = [item["content"] for item in resp.json()["items"]]
    assert contents == ["第0条回复", "第1条回复", "第2条回复"]


def test_sent_reply_visible_in_ticket_detail(client, customer, agent, ticket):
    """Customer detail view must also only surface sent replies."""
    reply = _create_reply(client, ticket["id"], agent["headers"]).json()
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/review",
        json={"approved": True},
        headers=agent["headers"],
    )
    client.post(
        f"/tickets/{ticket['id']}/replies/{reply['id']}/send",
        headers=agent["headers"],
    )
    resp = client.get(f"/tickets/{ticket['id']}", headers=customer["headers"])
    assert len(resp.json()["replies"]) == 1
    assert resp.json()["replies"][0]["status"] == "sent"
