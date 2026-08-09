"""Password hashing and JWT token helpers.

Passwords are hashed with bcrypt directly (not passlib, which is incompatible
with bcrypt>=5). Tokens carry only ``sub`` (user id), ``role`` and ``exp``.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
import jwt

from .config import ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, JWT_SECRET_KEY

# bcrypt only uses the first 72 bytes of a password. Enforce this at the
# schema layer too, so the two limits stay in sync.
BCRYPT_MAX_PASSWORD_BYTES = 72
# Minimum password length enforced by registration.
MIN_PASSWORD_LENGTH = 8


class PasswordHashError(ValueError):
    """Raised when a password cannot be hashed or verified."""


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise PasswordHashError("password must be a string")
    if len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise PasswordHashError(
            f"password cannot exceed {BCRYPT_MAX_PASSWORD_BYTES} bytes"
        )
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Return True when the plaintext matches the stored hash."""
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    if len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        # Malformed stored hash: fail closed.
        return False


def create_access_token(user_id: UUID, role: str) -> str:
    """Sign a short-lived JWT containing only ``sub``, ``role`` and ``exp``."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


class InvalidTokenError(ValueError):
    """Raised when a token is missing, malformed, invalid or expired."""


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a token; raise ``InvalidTokenError`` otherwise."""
    if not token:
        raise InvalidTokenError("token is required")
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("token is invalid") from exc

    if "sub" not in payload or "role" not in payload:
        raise InvalidTokenError("token is missing required claims")
    return payload
