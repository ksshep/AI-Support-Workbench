"""Authentication and RBAC tests (W1-B).

Covers registration, login, /auth/me, token handling, role checks and the
security rule that password hashes and secrets never leave the server.
"""

import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.app.config import JWT_ALGORITHM, JWT_SECRET_KEY
from backend.app.models import User
from backend.app.security import hash_password

REGISTER_BODY = {
    "email": "user@example.com",
    "password": "password123",
    "name": "测试用户",
}


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

def test_register_success(client):
    resp = client.post("/auth/register", json=REGISTER_BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["email"] == "user@example.com"
    assert body["user"]["name"] == "测试用户"
    assert body["user"]["role"] == "customer"
    assert "password_hash" not in resp.text
    assert "password" not in resp.text


def test_register_duplicate_email_conflict(client):
    client.post("/auth/register", json=REGISTER_BODY)
    resp = client.post("/auth/register", json=REGISTER_BODY)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "conflict"


def test_register_email_case_insensitive(client):
    first = client.post("/auth/register", json=REGISTER_BODY)
    assert first.status_code == 201
    dup = {**REGISTER_BODY, "email": "USER@Example.com"}
    assert client.post("/auth/register", json=dup).status_code == 409


def test_register_rejects_short_password(client):
    bad = {**REGISTER_BODY, "password": "short"}
    assert client.post("/auth/register", json=bad).status_code == 422


def test_register_rejects_invalid_email(client):
    bad = {**REGISTER_BODY, "email": "not-an-email"}
    assert client.post("/auth/register", json=bad).status_code == 422


def test_register_cannot_forge_role(client):
    """The request body cannot choose the role; registration is always customer."""
    for role in ("agent", "admin"):
        forged = {**REGISTER_BODY, "email": f"{role}@example.com", "role": role}
        resp = client.post("/auth/register", json=forged)
        assert resp.status_code == 201
        assert resp.json()["user"]["role"] == "customer"


def test_password_stored_hashed_not_plaintext(client, db):
    client.post("/auth/register", json=REGISTER_BODY)
    user = db.query(User).filter_by(email="user@example.com").one()
    assert user.password_hash != "password123"
    assert user.password_hash.startswith("$2")
    # The hash must be a valid bcrypt hash that verifies the original password.
    from backend.app.security import verify_password

    assert verify_password("password123", user.password_hash)


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def _login(client, username="user@example.com", password="password123"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def test_login_success_returns_token(client):
    client.post("/auth/register", json=REGISTER_BODY)
    resp = _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["user"]["role"] == "customer"


def test_login_wrong_password_401(client):
    client.post("/auth/register", json=REGISTER_BODY)
    assert _login(client, password="wrong-password").status_code == 401


def test_login_unknown_email_401(client):
    resp = _login(client, username="nobody@example.com")
    assert resp.status_code == 401
    # Same error shape as wrong password: no "user not found" leak.
    wrong = _login(client, username="nobody@example.com", password="x" * 10)
    assert resp.json() == wrong.json()


def test_login_error_uniform_for_unknown_email_and_wrong_password(client):
    client.post("/auth/register", json=REGISTER_BODY)
    unknown = _login(client, username="ghost@example.com")
    wrong = _login(client, password="totally-wrong")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_login_inactive_user_403(client, db):
    client.post("/auth/register", json=REGISTER_BODY)
    user = db.query(User).filter_by(email="user@example.com").one()
    user.is_active = False
    db.commit()
    assert _login(client).status_code == 403


# --------------------------------------------------------------------------
# /auth/me
# --------------------------------------------------------------------------

def _register_and_token(client) -> str:
    client.post("/auth/register", json=REGISTER_BODY)
    return _login(client).json()["access_token"]


def test_me_success(client):
    token = _register_and_token(client)
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "user@example.com"
    assert body["role"] == "customer"
    assert "password_hash" not in resp.text


def test_me_missing_token_401(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_invalid_token_401(client):
    resp = client.get(
        "/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_me_malformed_scheme_401(client):
    resp = client.get(
        "/auth/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert resp.status_code == 401


def test_me_expired_token_401(client):
    client.post("/auth/register", json=REGISTER_BODY)
    expired = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "role": "customer",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_me_token_without_role_claim_401(client):
    missing_role = jwt.encode(
        {"sub": "00000000-0000-0000-0000-000000000000",
         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    resp = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {missing_role}"}
    )
    assert resp.status_code == 401


def test_me_token_for_deleted_user_401(client, db):
    token = _register_and_token(client)
    user = db.query(User).filter_by(email="user@example.com").one()
    db.delete(user)
    db.commit()
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# RBAC
# --------------------------------------------------------------------------

def _create_user_direct(db, email, role):
    user = User(
        email=email,
        password_hash=hash_password("password123"),
        name=f"{role}用户",
        role=role,
    )
    db.add(user)
    db.commit()
    return user


def _login_role(client, db, email, role) -> str:
    _create_user_direct(db, email, role)
    resp = client.post(
        "/auth/login",
        data={"username": email, "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_probe_authenticated_requires_login(client):
    assert client.get("/probe/authenticated").status_code == 401


def test_customer_role_recognized(client, db):
    token = _login_role(client, db, "customer@example.com", "customer")
    resp = client.get("/probe/authenticated", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "customer"


def test_agent_role_recognized(client, db):
    token = _login_role(client, db, "agent@example.com", "agent")
    resp = client.get("/probe/agent", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "agent"


def test_admin_role_recognized(client, db):
    token = _login_role(client, db, "admin@example.com", "admin")
    resp = client.get("/probe/admin", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_customer_cannot_access_agent_endpoint_403(client, db):
    token = _login_role(client, db, "customer@example.com", "customer")
    resp = client.get("/probe/agent", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_customer_cannot_access_admin_endpoint_403(client, db):
    token = _login_role(client, db, "customer@example.com", "customer")
    resp = client.get("/probe/admin", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_agent_cannot_access_admin_endpoint_403(client, db):
    token = _login_role(client, db, "agent@example.com", "agent")
    resp = client.get("/probe/admin", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_admin_can_access_agent_endpoint(client, db):
    token = _login_role(client, db, "admin@example.com", "admin")
    resp = client.get("/probe/agent", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# --------------------------------------------------------------------------
# Secrets never leak
# --------------------------------------------------------------------------

def test_password_hash_not_in_response(client):
    client.post("/auth/register", json=REGISTER_BODY)
    token = _login(client).json()["access_token"]
    for path, headers in [
        ("/auth/me", {"Authorization": f"Bearer {token}"}),
        ("/auth/login", {"Content-Type": "application/x-www-form-urlencoded"}),
    ]:
        resp = client.get(path, headers=headers) if path == "/auth/me" else _login(client)
        assert "password_hash" not in resp.text


def test_register_response_never_echoes_password(client):
    resp = client.post("/auth/register", json=REGISTER_BODY)
    assert "password123" not in resp.text
    assert "password" not in resp.text.lower()


def test_token_payload_contains_no_secrets(client):
    token = _register_and_token(client)
    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    assert "sub" in payload
    assert "role" in payload
    assert "exp" in payload
    assert "password" not in payload
    assert "email" not in payload


# --------------------------------------------------------------------------
# Logs never contain secrets
# --------------------------------------------------------------------------

def test_no_secrets_in_request_logs(client, caplog):
    """Register/login/me must not emit logs containing the plaintext password."""
    with caplog.at_level("INFO"):
        resp = client.post("/auth/register", json=REGISTER_BODY)
        assert resp.status_code == 201
        token = _login(client).json()["access_token"]
        client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    log_text = "\n".join(r.message for r in caplog.records)
    assert "password123" not in log_text
