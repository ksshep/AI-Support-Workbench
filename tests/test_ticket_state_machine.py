"""State-machine transition tests (W2-A).

Covers the explicit transition table end-to-end over the HTTP layer: legal
flows, illegal transitions, role gates, and audit writes per transition.
"""

import pytest

from backend.app.models import AuditLog


def _transition(client, ticket_id, event, headers, expect):
    resp = client.post(
        f"/tickets/{ticket_id}/transition", json={"event": event}, headers=headers
    )
    assert resp.status_code == expect
    return resp


def test_open_to_in_review_by_agent(client, customer, agent, ticket):
    resp = _transition(
        client, ticket["id"], "start_review", agent["headers"], 200
    )
    assert resp.json()["status"] == "in_review"
    assert "reply" in resp.json()["allowed_events"]
    assert "cancel" in resp.json()["allowed_events"]


def test_open_to_canceled_by_customer(client, customer, ticket):
    resp = _transition(client, ticket["id"], "cancel", customer["headers"], 200)
    assert resp.json()["status"] == "canceled"
    assert resp.json()["allowed_events"] == []


def test_in_review_to_replied(client, customer, agent, ticket):
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    resp = _transition(client, ticket["id"], "reply", agent["headers"], 200)
    assert resp.json()["status"] == "replied"


def test_in_review_to_canceled_by_agent(client, customer, agent, ticket):
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    resp = _transition(client, ticket["id"], "cancel", agent["headers"], 200)
    assert resp.json()["status"] == "canceled"


def test_replied_to_closed(client, customer, agent, ticket):
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    _transition(client, ticket["id"], "reply", agent["headers"], 200)
    resp = _transition(client, ticket["id"], "close", agent["headers"], 200)
    assert resp.json()["status"] == "closed"
    assert resp.json()["allowed_events"] == []


def test_illegal_open_to_close_returns_400_with_help(client, customer, agent, ticket):
    resp = _transition(client, ticket["id"], "close", agent["headers"], 400)
    detail = resp.json()["detail"]
    assert detail["code"] == "invalid_state_transition"
    assert detail["current_status"] == "open"
    assert detail["allowed_events"] == ["start_review", "cancel"]


def test_unknown_event_400(client, customer, agent, ticket):
    resp = _transition(client, ticket["id"], "explode", agent["headers"], 400)
    assert resp.json()["detail"]["code"] == "invalid_input"


def test_terminal_state_rejects_every_event(client, customer, agent, ticket):
    _transition(client, ticket["id"], "cancel", customer["headers"], 200)
    for event in ("start_review", "reply", "close", "cancel"):
        resp = _transition(client, ticket["id"], event, agent["headers"], 400)
        assert resp.json()["detail"]["code"] == "invalid_state_transition"


def test_customer_cannot_start_review(client, customer, ticket):
    resp = _transition(client, ticket["id"], "start_review", customer["headers"], 403)
    assert resp.json()["detail"]["code"] == "forbidden"


def test_customer_cannot_close(client, customer, agent, ticket):
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    resp = _transition(client, ticket["id"], "close", customer["headers"], 403)
    assert resp.json()["detail"]["code"] == "forbidden"


def test_customer_cannot_cancel_after_start_review(client, customer, agent, ticket):
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    resp = _transition(client, ticket["id"], "cancel", customer["headers"], 403)


def test_admin_can_transition(client, customer, admin, ticket):
    resp = _transition(client, ticket["id"], "start_review", admin["headers"], 200)
    assert resp.json()["status"] == "in_review"


def test_transition_missing_ticket_404(client, agent):
    resp = client.post(
        "/tickets/00000000-0000-0000-0000-000000000000/transition",
        json={"event": "start_review"},
        headers=agent["headers"],
    )
    assert resp.status_code == 404


def test_transition_requires_login(client, ticket):
    resp = client.post(
        f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}
    )
    assert resp.status_code == 401


def test_transition_writes_audit_log(client, customer, agent, db, ticket):
    before = db.query(AuditLog).count()
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    after = db.query(AuditLog).count()
    assert after == before + 1

    log = db.query(AuditLog).order_by(AuditLog.created_at.desc()).first()
    assert log.action == "ticket.status_changed"
    assert log.entity_type == "ticket"
    assert str(log.entity_id) == ticket["id"]
    assert log.old_value == {"status": "open"}
    assert log.new_value == {"status": "in_review", "event": "start_review"}
    assert log.actor_id is not None


def test_repeated_transition_does_not_corrupt(client, customer, agent, ticket):
    """Firing the same event twice: first succeeds, second is a clean 400."""
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    _transition(client, ticket["id"], "start_review", agent["headers"], 400)
    resp = client.get(f"/tickets/{ticket['id']}", headers=customer["headers"])
    assert resp.json()["status"] == "in_review"


def test_full_lifecycle_audits_present(client, customer, agent, db, ticket):
    _transition(client, ticket["id"], "start_review", agent["headers"], 200)
    _transition(client, ticket["id"], "reply", agent["headers"], 200)
    _transition(client, ticket["id"], "close", agent["headers"], 200)

    actions = [
        log.action for log in db.query(AuditLog).order_by(AuditLog.created_at.asc())
    ]
    assert "ticket.created" in actions
    assert actions.count("ticket.status_changed") == 3
