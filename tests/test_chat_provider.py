"""ChatProvider abstraction tests (W3-A).

Covers the fake provider's deterministic structured output, the JSON parsing /
Pydantic validation gate, the failure simulation knobs, and that the factory
builds the provider the environment selects.
"""

import pytest

from backend.app.chat_provider import (
    FakeChatProvider,
    ProviderConfigError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from backend.app.provider_factory import (
    _create_chat_provider,
    get_chat_provider,
    reset_chat_provider_cache,
)
from backend.app.schemas.ai_analysis import TicketAnalysis


def test_fake_provider_returns_validated_structured_output():
    provider = FakeChatProvider()
    result = provider.extract_structured("system", "user", TicketAnalysis)
    assert result["category"] == "technical"
    assert result["priority"] == "high"
    assert result["sentiment"] == "negative"
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["summary"]
    assert result["reason"]


def test_fake_provider_deterministic():
    provider = FakeChatProvider()
    first = provider.extract_structured("s", "u", TicketAnalysis)
    second = provider.extract_structured("s", "u", TicketAnalysis)
    assert first == second


def test_fake_provider_rejects_invalid_json():
    provider = FakeChatProvider(raw_output="this is not json")
    with pytest.raises(StructuredOutputError):
        provider.extract_structured("s", "u", TicketAnalysis)


def test_fake_provider_rejects_missing_field():
    provider = FakeChatProvider(
        raw_output='{"category": "technical"}'
    )
    with pytest.raises(StructuredOutputError):
        provider.extract_structured("s", "u", TicketAnalysis)


def test_fake_provider_rejects_out_of_range_confidence():
    provider = FakeChatProvider(
        raw_output=(
            '{"category": "technical", "summary": "s", "priority": "high", '
            '"sentiment": "negative", "confidence": 1.5, "reason": "r"}'
        )
    )
    with pytest.raises(StructuredOutputError):
        provider.extract_structured("s", "u", TicketAnalysis)


def test_fake_provider_rejects_invalid_priority():
    provider = FakeChatProvider(
        raw_output=(
            '{"category": "technical", "summary": "s", "priority": "extreme", '
            '"sentiment": "negative", "confidence": 0.5, "reason": "r"}'
        )
    )
    with pytest.raises(StructuredOutputError):
        provider.extract_structured("s", "u", TicketAnalysis)


def test_fake_provider_rejects_unknown_category():
    provider = FakeChatProvider(
        raw_output=(
            '{"category": "mystery", "summary": "s", "priority": "high", '
            '"sentiment": "negative", "confidence": 0.5, "reason": "r"}'
        )
    )
    with pytest.raises(StructuredOutputError):
        provider.extract_structured("s", "u", TicketAnalysis)


def test_fake_provider_can_simulate_timeout():
    provider = FakeChatProvider(
        fail_with=ProviderTimeoutError("模拟超时"), failures_remaining=1
    )
    with pytest.raises(ProviderTimeoutError):
        provider.extract_structured("s", "u", TicketAnalysis)
    # Failure budget exhausted: next call succeeds.
    result = provider.extract_structured("s", "u", TicketAnalysis)
    assert result["category"] == "technical"


def test_fake_provider_can_fail_then_recover():
    provider = FakeChatProvider(
        fail_with=StructuredOutputError("模拟坏输出"), failures_remaining=2
    )
    for _ in range(2):
        with pytest.raises(StructuredOutputError):
            provider.extract_structured("s", "u", TicketAnalysis)
    result = provider.extract_structured("s", "u", TicketAnalysis)
    assert result["priority"] == "high"


def test_factory_defaults_to_fake():
    reset_chat_provider_cache()
    provider = get_chat_provider()
    assert isinstance(provider, FakeChatProvider)
    reset_chat_provider_cache()


def test_compatible_provider_requires_env_config():
    """A compatible provider without base URL / key / model must fail closed."""
    with pytest.raises(ProviderConfigError):
        from backend.app.chat_provider import OpenAICompatibleChatProvider

        OpenAICompatibleChatProvider(base_url="", api_key="", model="")


def test_unknown_provider_mode_falls_back_to_fake(monkeypatch):
    monkeypatch.setattr(
        "backend.app.provider_factory.CHAT_PROVIDER", "does-not-exist"
    )
    provider = _create_chat_provider()
    assert isinstance(provider, FakeChatProvider)
