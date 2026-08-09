"""FastAPI application entrypoint."""

from fastapi import FastAPI

from .config import CHAT_PROVIDER, EMBEDDING_PROVIDER

app = FastAPI(
    title="AI Support Workbench",
    description="面向企业客服团队的 AI 工单与客服协同平台 API",
    version="0.1.0",
)


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
