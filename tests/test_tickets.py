"""Ticket CRUD, permission-scoping, pagination and filter tests (W2-A).

These exercise the HTTP layer end-to-end against the isolated test database:
creation rules per role, list/detail visibility, PATCH field permissions,
and SQL pagination/filtering.
"""

import pytest

CREATE_BODY = {
    "title": "无法登录系统",
    "description": "输入正确密码后仍然无法登录",
    "classification": "technical",
}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# POST /tickets — creation and role gates
# --------------------------------------------------------------------------

def test_create_ticket_requires_login(client):
    resp = client.post("/tickets", json=CREATE_BODY)
    assert resp.status_code == 401


def test_customer_can_create_ticket(client, customer):
    resp = client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "无法登录系统"
    assert body["status"] == "open"
    assert body["priority"] == "normal"
    assert body["classification"] == "technical"
    assert "customer_id" not in body
    assert "password_hash" not in resp.text


def test_customer_cannot_forge_customer_id(client, customer, db):
    """The request body must never be able to set customer_id.

    ``extra="forbid"`` rejects the unknown field outright (422), so a client
    cannot smuggle an ownership change in.
    """
    forged = {**CREATE_BODY, "customer_id": "00000000-0000-0000-0000-000000000000"}
    resp = client.post("/tickets", json=forged, headers=customer["headers"])
    assert resp.status_code == 422
    from backend.app.models import Ticket

    assert db.query(Ticket).count() == 0


def test_agent_cannot_create_ticket(client, agent):
    resp = client.post("/tickets", json=CREATE_BODY, headers=agent["headers"])
    assert resp.status_code == 403


def test_admin_cannot_create_ticket(client, admin):
    resp = client.post("/tickets", json=CREATE_BODY, headers=admin["headers"])
    assert resp.status_code == 403


def test_create_ticket_missing_title(client, customer):
    resp = client.post(
        "/tickets", json={"description": "没有标题"}, headers=customer["headers"]
    )
    assert resp.status_code == 422


def test_create_ticket_blank_title(client, customer):
    resp = client.post(
        "/tickets", json={"title": "   ", "description": "标题空白"},
        headers=customer["headers"],
    )
    assert resp.status_code == 422


def test_create_ticket_title_too_long(client, customer):
    resp = client.post(
        "/tickets", json={"title": "x" * 201, "description": "超长标题"},
        headers=customer["headers"],
    )
    assert resp.status_code == 422


def test_create_ticket_missing_description(client, customer):
    resp = client.post(
        "/tickets", json={"title": "没有描述"}, headers=customer["headers"]
    )
    assert resp.status_code == 422


def test_create_ticket_description_too_long(client, customer):
    resp = client.post(
        "/tickets", json={"title": "标题", "description": "x" * 10001},
        headers=customer["headers"],
    )
    assert resp.status_code == 422


def test_create_ticket_invalid_classification(client, customer):
    resp = client.post(
        "/tickets",
        json={**CREATE_BODY, "classification": "x" * 51},
        headers=customer["headers"],
    )
    assert resp.status_code == 422


def test_create_ticket_optional_classification_defaults_empty(client, customer):
    resp = client.post(
        "/tickets", json={"title": "无分类", "description": "描述"},
        headers=customer["headers"],
    )
    assert resp.status_code == 201
    assert resp.json()["classification"] == ""


def test_create_ticket_response_has_no_secrets(client, customer):
    resp = client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    text = resp.text
    assert "password" not in text.lower()
    assert "token" not in text.lower()
    assert "apikey" not in text.lower()


# --------------------------------------------------------------------------
# GET /tickets — visibility scoping
# --------------------------------------------------------------------------

def test_list_requires_login(client):
    assert client.get("/tickets").status_code == 401


def test_customer_only_sees_own_tickets(client, customer, other_customer):
    client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    client.post(
        "/tickets",
        json={"title": "别人的单", "description": "别的客户提交"},
        headers=other_customer["headers"],
    )
    resp = client.get("/tickets", headers=customer["headers"])
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "无法登录系统"


def test_agent_sees_all_tickets(client, customer, other_customer, agent):
    client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    client.post(
        "/tickets",
        json={"title": "第二个单", "description": "其他客户"},
        headers=other_customer["headers"],
    )
    resp = client.get("/tickets", headers=agent["headers"])
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


def test_admin_sees_all_tickets(client, customer, other_customer, admin):
    client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    client.post(
        "/tickets",
        json={"title": "第二个单", "description": "其他客户"},
        headers=other_customer["headers"],
    )
    resp = client.get("/tickets", headers=admin["headers"])
    assert len(resp.json()["items"]) == 2


# --------------------------------------------------------------------------
# GET /tickets/{id} — detail
# --------------------------------------------------------------------------

def test_get_ticket_requires_login(client):
    assert client.get("/tickets/00000000-0000-0000-0000-000000000000").status_code == 401


def test_get_missing_ticket_404(client, customer):
    resp = client.get(
        "/tickets/00000000-0000-0000-0000-000000000000", headers=customer["headers"]
    )
    assert resp.status_code == 404


def test_customer_cannot_view_other_customers_ticket(client, customer, other_customer):
    created = client.post("/tickets", json=CREATE_BODY, headers=customer["headers"]).json()
    resp = client.get(f"/tickets/{created['id']}", headers=other_customer["headers"])
    assert resp.status_code == 403


def test_agent_can_view_any_ticket(client, customer, agent, ticket):
    resp = client.get(f"/tickets/{ticket['id']}", headers=agent["headers"])
    assert resp.status_code == 200
    assert resp.json()["title"] == ticket["title"]


def test_detail_returns_replies_and_audit_in_order(client, customer, db, ticket):
    from backend.app.models import AuditLog, Ticket, TicketReply

    created = db.query(Ticket).filter_by(id=ticket["id"]).one()
    db.add(TicketReply(
        ticket_id=created.id,
        sender_id=created.customer_id,
        content="第一条回复",
        status="sent",
    ))
    db.add(AuditLog(
        actor_id=created.customer_id,
        action="ticket.created",
        entity_type="ticket",
        entity_id=created.id,
        new_value={"status": "open"},
    ))
    db.commit()

    resp = client.get(f"/tickets/{ticket['id']}", headers=customer["headers"])
    body = resp.json()
    assert len(body["replies"]) == 1
    assert body["replies"][0]["content"] == "第一条回复"
    assert body["replies"][0]["sender_name"] == "客户一"
    assert body["audit"][0]["action"] == "ticket.created"
    assert "password" not in resp.text.lower()


def test_detail_without_replies_or_audit_still_returns(client, customer, ticket):
    resp = client.get(f"/tickets/{ticket['id']}", headers=customer["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["replies"] == []
    assert isinstance(body["audit"], list)


# --------------------------------------------------------------------------
# PATCH /tickets/{id} — field-level permissions
# --------------------------------------------------------------------------

def test_patch_requires_login(client, ticket):
    resp = client.patch(f"/tickets/{ticket['id']}", json={"title": "新标题"})
    assert resp.status_code == 401


def test_customer_updates_own_open_ticket(client, customer, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"title": "更新后的标题", "description": "更新后的描述"},
        headers=customer["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "更新后的标题"
    assert body["description"] == "更新后的描述"
    assert body["status"] == "open"


def test_customer_cannot_update_other_ticket(client, customer, other_customer, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"title": "越权修改"},
        headers=other_customer["headers"],
    )
    assert resp.status_code == 403


def test_customer_cannot_change_priority(client, customer, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"priority": "high"},
        headers=customer["headers"],
    )
    assert resp.status_code == 403


def test_customer_cannot_change_classification(client, customer, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"classification": "billing"},
        headers=customer["headers"],
    )
    assert resp.status_code == 403


def test_customer_cannot_edit_non_open_ticket(client, customer, agent, ticket):
    client.post(
        f"/tickets/{ticket['id']}/transition",
        json={"event": "start_review"},
        headers=agent["headers"],
    )
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"title": "处理中改标题"},
        headers=customer["headers"],
    )
    assert resp.status_code == 409


def test_agent_can_change_priority_and_classification(client, customer, agent, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"priority": "high", "classification": "billing"},
        headers=agent["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["priority"] == "high"
    assert body["classification"] == "billing"


def test_agent_invalid_priority_rejected(client, customer, agent, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"priority": "extreme"},
        headers=agent["headers"],
    )
    assert resp.status_code == 422


def test_admin_can_update_ticket(client, customer, admin, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"title": "管理员改的", "priority": "urgent"},
        headers=admin["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "管理员改的"
    assert resp.json()["priority"] == "urgent"


def test_empty_patch_returns_400(client, customer, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}", json={}, headers=customer["headers"]
    )
    assert resp.status_code == 400


def test_patch_unknown_field_rejected(client, customer, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"status": "closed"},
        headers=customer["headers"],
    )
    assert resp.status_code == 422


def test_patch_customer_id_rejected(client, customer, ticket):
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"customer_id": "00000000-0000-0000-0000-000000000000"},
        headers=customer["headers"],
    )
    assert resp.status_code == 422


def test_patch_no_change_writes_no_new_audit(client, customer, db, ticket):
    from backend.app.models import AuditLog

    before = db.query(AuditLog).count()
    resp = client.patch(
        f"/tickets/{ticket['id']}",
        json={"title": "无法登录系统"},  # same value already stored
        headers=customer["headers"],
    )
    assert resp.status_code == 200
    after = db.query(AuditLog).count()
    assert after == before


# --------------------------------------------------------------------------
# Pagination and filtering
# --------------------------------------------------------------------------

@pytest.fixture
def three_tickets(client, customer):
    for i in range(3):
        client.post(
            "/tickets",
            json={"title": f"工单{i}", "description": f"描述{i}"},
            headers=customer["headers"],
        )


def test_default_pagination(client, customer, three_tickets):
    resp = client.get("/tickets", headers=customer["headers"])
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_page_size_upper_bound(client, customer, three_tickets):
    resp = client.get("/tickets?page_size=100", headers=customer["headers"])
    assert resp.status_code == 200
    assert resp.json()["page_size"] == 100


def test_page_size_over_100_400(client, customer):
    resp = client.get("/tickets?page_size=101", headers=customer["headers"])
    assert resp.status_code == 400


def test_page_size_zero_400(client, customer):
    resp = client.get("/tickets?page_size=0", headers=customer["headers"])
    assert resp.status_code == 400


def test_page_less_than_one_400(client, customer):
    resp = client.get("/tickets?page=0", headers=customer["headers"])
    assert resp.status_code == 400
    resp = client.get("/tickets?page=-1", headers=customer["headers"])
    assert resp.status_code == 400


def test_pagination_slices_rows(client, customer):
    for i in range(5):
        client.post(
            "/tickets",
            json={"title": f"工单{i}", "description": f"描述{i}"},
            headers=customer["headers"],
        )
    resp = client.get("/tickets?page=1&page_size=2", headers=customer["headers"])
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["pages"] == 3
    assert body["items"][0]["title"] == "工单4"  # created_at desc


def test_status_filter(client, customer, agent):
    client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    created = client.get("/tickets", headers=customer["headers"]).json()["items"][0]
    client.post(
        f"/tickets/{created['id']}/transition",
        json={"event": "cancel"},
        headers=customer["headers"],
    )
    resp = client.get("/tickets?status=open", headers=customer["headers"])
    assert len(resp.json()["items"]) == 0
    resp = client.get("/tickets?status=canceled", headers=customer["headers"])
    assert len(resp.json()["items"]) == 1


def test_priority_filter(client, customer, agent):
    client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    created = client.get("/tickets", headers=customer["headers"]).json()["items"][0]
    client.patch(
        f"/tickets/{created['id']}",
        json={"priority": "high"},
        headers=agent["headers"],
    )
    resp = client.get("/tickets?priority=high", headers=customer["headers"])
    assert len(resp.json()["items"]) == 1
    resp = client.get("/tickets?priority=low", headers=customer["headers"])
    assert len(resp.json()["items"]) == 0


def test_classification_filter(client, customer, agent):
    client.post("/tickets", json=CREATE_BODY, headers=customer["headers"])
    resp = client.get("/tickets?classification=technical", headers=customer["headers"])
    assert len(resp.json()["items"]) == 1
    resp = client.get("/tickets?classification=billing", headers=customer["headers"])
    assert len(resp.json()["items"]) == 0


def test_empty_result_returns_empty_items(client, customer):
    resp = client.get("/tickets", headers=customer["headers"])
    body = resp.json()
    assert resp.status_code == 200
    assert body["items"] == []
    assert body["total"] == 0


def test_list_response_shape(client, customer):
    resp = client.get("/tickets", headers=customer["headers"])
    body = resp.json()
    for key in ("items", "total", "page", "page_size", "pages"):
        assert key in body
