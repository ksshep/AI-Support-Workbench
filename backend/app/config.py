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


def get_redis_url() -> str:
    """Return the current Redis URL, read lazily from the environment.

    Read on every call (not cached at import time) so tests can point the
    queue at an ephemeral fakeredis server between cases.
    """
    return os.environ.get("REDIS_URL") or "redis://localhost:6379/0"

# Chat provider selection: "fake" or "compatible". Business code never names
# a vendor: the "compatible" provider speaks the OpenAI-compatible HTTP
# protocol and reads base URL / API key / model from the environment.
CHAT_PROVIDER = (os.getenv("CHAT_PROVIDER") or "fake").strip().lower()
CHAT_BASE_URL = os.getenv("CHAT_BASE_URL", "")
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL", "")
CHAT_TIMEOUT_SECONDS = int(os.getenv("CHAT_TIMEOUT_SECONDS", "30"))

# Embedding provider selection: "fake" or "compatible".
EMBEDDING_PROVIDER = (os.getenv("EMBEDDING_PROVIDER") or "fake").strip().lower()
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
# Must match the VECTOR dimension on ``knowledge_chunks.embedding``.
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1536"))
EMBEDDING_TIMEOUT_SECONDS = int(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "30"))

# JWT authentication.
# ``JWT_SECRET_KEY`` must be overridden outside local development. The default
# is an obvious 32-byte placeholder so that a production deploy cannot
# accidentally ship with a known key.
JWT_SECRET_KEY = os.getenv(
    "JWT_SECRET_KEY", "dev-only-placeholder-32-bytes-minimum!"
)
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# RQ retry policy.
RQ_MAX_RETRIES = int(os.getenv("RQ_MAX_RETRIES", "2"))
RQ_RETRY_DELAYS = [
    int(item)
    for item in (os.getenv("RQ_RETRY_DELAYS", "5,30") or "5,30").split(",")
    if item.strip().isdigit()
]
