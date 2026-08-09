"""Application-wide configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str, *, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


DATABASE_URL = _require("DATABASE_URL")

REDIS_URL = _require("REDIS_URL", default="redis://localhost:6379/0")

# Chat provider selection: "fake" or "compatible".
CHAT_PROVIDER = (os.getenv("CHAT_PROVIDER") or "fake").strip().lower()

# Embedding provider selection: "fake" or "compatible".
EMBEDDING_PROVIDER = (os.getenv("EMBEDDING_PROVIDER") or "fake").strip().lower()

EMBEDDING_DIMENSION = 1536

# JWT configuration (used from W1-B onwards; declared now so .env is complete).
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# RQ retry policy.
RQ_MAX_RETRIES = int(os.getenv("RQ_MAX_RETRIES", "2"))
RQ_RETRY_DELAYS = [
    int(item)
    for item in (os.getenv("RQ_RETRY_DELAYS", "5,30") or "5,30").split(",")
    if item.strip().isdigit()
]
