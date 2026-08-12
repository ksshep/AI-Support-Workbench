"""Embedding provider tests (W3-B).

Covers the fake provider's deterministic dimension, dimension validation,
the batch-count guarantee, the factory selection, and that the compatible
provider fails closed without configuration.
"""

import pytest

from backend.app.config import EMBEDDING_DIMENSION
from backend.app.embedding import (
    EmbeddingConfigError,
    EmbeddingDimensionError,
    EmbeddingInputError,
    EmbeddingOutputError,
    EmbeddingTimeoutError,
    FakeEmbeddingProvider,
)
from backend.app.provider_factory import (
    _create_embedding_provider,
    get_embedding_provider,
    reset_embedding_provider_cache,
)


def test_fake_provider_returns_correct_dimension():
    provider = FakeEmbeddingProvider()
    vectors = provider.embed_texts(["hello", "world"])
    assert len(vectors) == 2
    assert all(len(v) == EMBEDDING_DIMENSION for v in vectors)


def test_fake_provider_is_deterministic():
    provider = FakeEmbeddingProvider()
    first = provider.embed_texts(["同一个文本"])
    second = provider.embed_texts(["同一个文本"])
    assert first == second


def test_fake_provider_different_text_different_vector():
    provider = FakeEmbeddingProvider()
    a = provider.embed_texts(["文本A"])
    b = provider.embed_texts(["文本B"])
    assert a != b


def test_fake_provider_rejects_empty_text():
    provider = FakeEmbeddingProvider()
    with pytest.raises(EmbeddingInputError):
        provider.embed_texts(["  "])


def test_fake_provider_can_simulate_timeout():
    provider = FakeEmbeddingProvider(
        fail_with=EmbeddingTimeoutError("模拟超时"), failures_remaining=1
    )
    with pytest.raises(EmbeddingTimeoutError):
        provider.embed_texts(["hello"])
    # Failure budget exhausted: next call succeeds.
    vectors = provider.embed_texts(["hello"])
    assert len(vectors[0]) == EMBEDDING_DIMENSION


def test_fake_provider_wrong_dimension_fails_closed():
    provider = FakeEmbeddingProvider(dimension_override=8)
    vectors = provider.embed_texts(["short"])
    assert len(vectors[0]) == 8
    with pytest.raises(EmbeddingDimensionError):
        FakeEmbeddingProvider.validate_dimension(vectors[0])


def test_batch_mismatch_detected():
    from backend.app.embedding import EmbeddingProvider

    with pytest.raises(EmbeddingOutputError):
        EmbeddingProvider.validate_batch(["a", "b"], [[0.0] * EMBEDDING_DIMENSION])


def test_dimension_mismatch_detected():
    from backend.app.embedding import EmbeddingProvider

    with pytest.raises(EmbeddingDimensionError):
        EmbeddingProvider.validate_batch(["a"], [[0.0, 1.0]])


def test_factory_defaults_to_fake():
    reset_embedding_provider_cache()
    provider = get_embedding_provider()
    assert isinstance(provider, FakeEmbeddingProvider)
    reset_embedding_provider_cache()


def test_compatible_provider_requires_env_config():
    from backend.app.embedding import OpenAICompatibleEmbeddingProvider

    with pytest.raises(EmbeddingConfigError):
        OpenAICompatibleEmbeddingProvider(base_url="", api_key="", model="")


def test_unknown_provider_mode_falls_back_to_fake(monkeypatch):
    monkeypatch.setattr(
        "backend.app.provider_factory.EMBEDDING_PROVIDER", "does-not-exist"
    )
    provider = _create_embedding_provider()
    assert isinstance(provider, FakeEmbeddingProvider)
