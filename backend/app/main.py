"""FastAPI application entrypoint."""

from fastapi import Depends, FastAPI

from .api import auth
from .config import CHAT_PROVIDER, EMBEDDING_PROVIDER
from .deps import get_current_user, require_roles
from .models import User

app = FastAPI(
    title="AI Support Workbench",
    description="面向企业客服团队的 AI 工单与客服协同平台 API",
    version="0.1.0",
)

app.include_router(auth.router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug/providers", include_in_schema=False)
def debug_providers() -> dict[str, str]:
    """Report the configured provider modes (never reveals secrets)."""
    return {
        "chat_provider": CHAT_PROVIDER,
        "embedding_provider": EMBEDDING_PROVIDER,
    }


# --- Test-only role probes --------------------------------------------------
# These endpoints exist so W1-B can verify RBAC end-to-end and so Swagger has
# a concrete target for manual acceptance. They are replaced by the real
# staff/admin endpoints in later milestones.

@app.get("/probe/authenticated", tags=["system"])
def probe_authenticated(
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    return {"role": current_user.role}


@app.get("/probe/agent", tags=["system"])
def probe_agent(
    current_user: User = Depends(require_roles("agent", "admin")),
) -> dict[str, str]:
    return {"role": current_user.role}


@app.get("/probe/admin", tags=["system"])
def probe_admin(
    current_user: User = Depends(require_roles("admin")),
) -> dict[str, str]:
    return {"role": current_user.role}