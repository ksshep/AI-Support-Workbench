"""Provider factories (W3-A chat, W3-B embedding).

Business code asks for a ``ChatProvider`` through ``get_chat_provider()`` and
an ``EmbeddingProvider`` through ``get_embedding_provider()``; it never imports
a concrete vendor class. Selection comes from ``CHAT_PROVIDER`` /
``EMBEDDING_PROVIDER`` (``fake`` or ``compatible``). The same single function
is used by the web process and the RQ worker so both always use the same
provider configuration.
"""

from .chat_provider import (
    ChatProvider,
    FakeChatProvider,
    OpenAICompatibleChatProvider,
)
from .config import CHAT_PROVIDER, EMBEDDING_PROVIDER
from .embedding import (
    EmbeddingProvider,
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)

# Factory results are cached so the whole process shares one instance per
# provider type. Tests reset the caches after monkeypatching config values.
_chat_provider_cache: ChatProvider | None = None
_embedding_provider_cache: EmbeddingProvider | None = None


def get_chat_provider() -> ChatProvider:
    """Return the configured chat provider, creating it on first use."""
    global _chat_provider_cache
    if _chat_provider_cache is None:
        _chat_provider_cache = _create_chat_provider()
    return _chat_provider_cache


def _create_chat_provider() -> ChatProvider:
    if CHAT_PROVIDER == "compatible":
        return OpenAICompatibleChatProvider.from_env()
    return FakeChatProvider()


def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured embedding provider, creating it on first use."""
    global _embedding_provider_cache
    if _embedding_provider_cache is None:
        _embedding_provider_cache = _create_embedding_provider()
    return _embedding_provider_cache


def _create_embedding_provider() -> EmbeddingProvider:
    if EMBEDDING_PROVIDER == "compatible":
        return OpenAICompatibleEmbeddingProvider.from_env()
    return FakeEmbeddingProvider()


def reset_chat_provider_cache() -> None:
    """Drop the cached chat provider (used by tests between cases)."""
    global _chat_provider_cache
    _chat_provider_cache = None


def reset_embedding_provider_cache() -> None:
    """Drop the cached embedding provider (used by tests between cases)."""
    global _embedding_provider_cache
    _embedding_provider_cache = None
