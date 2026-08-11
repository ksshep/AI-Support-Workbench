"""Ticket evaluation tests (W2-B).

Covers the full lifecycle: customer evaluates a closed ticket once, all the
403/400/409/422 guards, UNIQUE(ticket_id) dedup, and audit logging.
"""

import pytest

from backend.app.models import AuditLog, Evaluation


def _close_ticket(client, customer, agent, ticket):
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    client.post(f"/tickets/{ticket['id']}/transition", json={"event": "reply"}, headers=agent["headers"])
    return client.post(
        f"/tickets/{ticket['id']}/transition", json={"event": "close"}, headers=agent["headers"]
    )


def _evaluate(client, ticket_id, headers, rating=5, comment="问题已解决"):
    return client.post(
        f"/tickets/{ticket_id}/evaluation",
        json={"rating": rating, "comment": comment},
        headers=headers,
    )


def test_customer_evaluates_closed_ticket(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    resp = _evaluate(client, ticket["id"], customer["headers"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["rating"] == 5
    assert body["comment"] == "问题已解决"
    assert "customer_id" not in body


def test_agent_evaluate_forbidden(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    resp = _evaluate(client, ticket["id"], agent["headers"])
    assert resp.status_code == 403


def test_admin_evaluate_forbidden(client, customer, agent, admin, ticket):
    _close_ticket(client, customer, agent, ticket)
    resp = _evaluate(client, ticket["id"], admin["headers"])
    assert resp.status_code == 403


def test_other_customer_evaluate_forbidden(client, customer, other_customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    resp = _evaluate(client, ticket["id"], other_customer["headers"])
    assert resp.status_code == 403


def test_evaluate_requires_login(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    resp = client.post(
        f"/tickets/{ticket['id']}/evaluation",
        json={"rating": 5},
    )
    assert resp.status_code == 401


def test_missing_ticket_404(client, customer):
    resp = _evaluate(client, "00000000-0000-0000-0000-000000000000", customer["headers"])
    assert resp.status_code == 404


@pytest.mark.parametrize("status_label", ["open", "in_review", "replied", "canceled"])
def test_non_closed_ticket_cannot_evaluate(client, customer, agent, ticket, status_label):
    if status_label == "in_review":
        client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
    elif status_label == "replied":
        client.post(f"/tickets/{ticket['id']}/transition", json={"event": "start_review"}, headers=agent["headers"])
        client.post(f"/tickets/{ticket['id']}/transition", json={"event": "reply"}, headers=agent["headers"])
    elif status_label == "canceled":
        client.post(f"/tickets/{ticket['id']}/transition", json={"event": "cancel"}, headers=customer["headers"])
    resp = _evaluate(client, ticket["id"], customer["headers"])
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_state_transition"


def test_rating_out_of_range_422(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    for rating in (0, 6):
        resp = _evaluate(client, ticket["id"], customer["headers"], rating=rating)
        assert resp.status_code == 422


def test_empty_comment_allowed(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    resp = _evaluate(client, ticket["id"], customer["headers"], comment=None)
    assert resp.status_code == 201
    assert resp.json()["comment"] is None


def test_duplicate_evaluation_409(client, customer, agent, db, ticket):
    from backend.app.models import User

    _close_ticket(client, customer, agent, ticket)
    first = _evaluate(client, ticket["id"], customer["headers"])
    second = _evaluate(client, ticket["id"], customer["headers"])
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "conflict"
    assert db.query(Evaluation).filter_by(ticket_id=ticket["id"]).count() == 1


def test_unique_ticket_constraint_enforced(client, customer, agent, db, ticket):
    """Concurrent duplicate evaluation is stopped by the DB constraint."""
    from sqlalchemy.exc import IntegrityError

    from backend.app.models import User

    _close_ticket(client, customer, agent, ticket)
    owner = db.query(User).filter_by(email=customer["email"]).one()
    db.add(Evaluation(ticket_id=ticket["id"], customer_id=owner.id, rating=4))
    db.commit()
    with pytest.raises(IntegrityError):
        db.add(Evaluation(ticket_id=ticket["id"], customer_id=owner.id, rating=3))
        db.flush()


def test_evaluation_writes_audit(client, customer, agent, db, ticket):
    from backend.app.models import User

    _close_ticket(client, customer, agent, ticket)
    before = db.query(AuditLog).count()
    _evaluate(client, ticket["id"], customer["headers"])
    assert db.query(AuditLog).count() == before + 1
    log = db.query(AuditLog).filter_by(action="evaluation.created").one()
    assert log.entity_type == "evaluation"
    assert log.new_value["rating"] == 5
    owner = db.query(User).filter_by(email=customer["email"]).one()
    assert log.actor_id == owner.id


def test_get_evaluation_owner_can_view(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    _evaluate(client, ticket["id"], customer["headers"])
    resp = client.get(f"/tickets/{ticket['id']}/evaluation", headers=customer["headers"])
    assert resp.status_code == 200
    assert resp.json()["rating"] == 5


def test_get_evaluation_staff_can_view(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    _evaluate(client, ticket["id"], customer["headers"])
    resp = client.get(f"/tickets/{ticket['id']}/evaluation", headers=agent["headers"])
    assert resp.status_code == 200
    assert resp.json()["customer_id"] is not None


def test_get_evaluation_other_customer_403(client, customer, other_customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    _evaluate(client, ticket["id"], customer["headers"])
    resp = client.get(f"/tickets/{ticket['id']}/evaluation", headers=other_customer["headers"])
    assert resp.status_code == 403


def test_get_evaluation_none_404(client, customer, agent, ticket):
    _close_ticket(client, customer, agent, ticket)
    resp = client.get(f"/tickets/{ticket['id']}/evaluation", headers=customer["headers"])
    assert resp.status_code == 404
