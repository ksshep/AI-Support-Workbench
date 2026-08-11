"""Chat provider factory (W3-A).

Business code asks for a ``ChatProvider`` through ``get_chat_provider()`` and
never imports a concrete vendor class directly. The selection comes from
``CHAT_PROVIDER`` (``fake`` or ``compatible``); a single function is used by
both the web process and the RQ worker so both always use the same provider
configuration.
"""

from .chat_provider import (
    ChatProvider,
    FakeChatProvider,
    OpenAICompatibleChatProvider,
)
from .config import CHAT_PROVIDER

# The factory result is cached so the whole process shares one provider
# instance (e.g. one set of timeout settings). Tests reset the cache through
# ``reset_chat_provider_cache`` after monkeypatching config values.
_chat_provider_cache: ChatProvider | None = None


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


def reset_chat_provider_cache() -> None:
    """Drop the cached instance (used by tests between cases)."""
    global _chat_provider_cache
    _chat_provider_cache = None
