"""Embedding provider abstraction (W3-B).

Mirrors the ``ChatProvider`` design: business code depends on the
``EmbeddingProvider`` interface, never on a vendor, a model id, an API key or
a base URL. The ``fake`` provider returns a deterministic vector so tests stay
offline; the ``compatible`` provider speaks the OpenAI-compatible
``/embeddings`` HTTP protocol and reads everything from the environment.

Every embedding vector is validated against ``EMBEDDING_DIMENSION`` before it
is written anywhere: a dimension mismatch means the vector cannot go into the
``knowledge_chunks.embedding`` column, so the ingestion task fails with a
recorded ``error_message`` instead of persisting bad data.
"""

import json
from abc import ABC, abstractmethod
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDING_TIMEOUT_SECONDS,
)


class EmbeddingProviderError(Exception):
    """Base class for every embedding-provider failure (retryable)."""


class EmbeddingTimeoutError(EmbeddingProviderError):
    """The provider did not answer within ``EMBEDDING_TIMEOUT_SECONDS``."""


class EmbeddingHTTPError(EmbeddingProviderError):
    """The provider returned a non-200 HTTP response or bad payload."""


class EmbeddingConfigError(EmbeddingProviderError):
    """The provider is not configured (missing base URL / API key / model)."""


class EmbeddingDimensionError(EmbeddingProviderError):
    """A returned vector has the wrong dimension for the database column."""


class EmbeddingOutputError(EmbeddingProviderError):
    """The provider returned fewer vectors than input texts (or none)."""


class EmbeddingInputError(EmbeddingProviderError):
    """An input text is empty or invalid."""


class EmbeddingProvider(ABC):
    """Port for embedding access used by the knowledge-ingestion worker."""

    name: str = "abstract"

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts and return one vector per text.

        Every returned vector must have exactly ``EMBEDDING_DIMENSION``
        entries, otherwise ``EmbeddingDimensionError`` is raised — the caller
        relies on this guarantee before inserting into pgvector.
        """

    @classmethod
    def validate_dimension(cls, vector: list[float]) -> None:
        """Fail closed when a vector cannot fit the database column."""
        if len(vector) != EMBEDDING_DIMENSION:
            raise EmbeddingDimensionError(
                f"向量维度 {len(vector)} 与数据库维度 {EMBEDDING_DIMENSION} 不一致"
            )

    @classmethod
    def validate_batch(cls, texts: list[str], vectors: list[list[float]]) -> None:
        """Validate a whole batch: one vector per text, correct dimension."""
        if len(vectors) != len(texts):
            raise EmbeddingOutputError(
                f"Embedding 返回 {len(vectors)} 个向量，期望 {len(texts)} 个"
            )
        for vector in vectors:
            cls.validate_dimension(vector)


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic, offline provider used by tests and local development.

    The vector is a deterministic hash of the text over a fixed-dimension
    space, so the same text always yields the same vector (needed by the
    similarity search tests) but different texts usually differ. ``fail_with``
    / ``failures_remaining`` simulate provider failures like the fake chat
    provider does.
    """

    name = "fake"

    def __init__(
        self,
        *,
        fail_with: EmbeddingProviderError | None = None,
        failures_remaining: int = 0,
        dimension_override: int | None = None,
    ) -> None:
        self.fail_with = fail_with
        self.failures_remaining = failures_remaining
        # Tests can force a wrong dimension to exercise the mismatch path.
        self._dimension = dimension_override or EMBEDDING_DIMENSION

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.fail_with is not None and self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise self.fail_with
        return [self._vector_for(text) for text in texts]

    def _vector_for(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise EmbeddingInputError("空文本无法生成向量")
        seed = 0
        for ch in text:
            seed = (seed * 31 + ord(ch)) & 0xFFFFFFFF
        # Normalise to [-1, 1] so cosine similarity is well defined.
        values = []
        for i in range(self._dimension):
            value = ((seed >> (i % 31)) & 1) * 2 - 1
            values.append(float(value))
        return values


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Real provider speaking the OpenAI-compatible ``/embeddings`` API.

    Configured exclusively from the environment (``EMBEDDING_BASE_URL``,
    ``EMBEDDING_API_KEY``, ``EMBEDDING_MODEL``, ``EMBEDDING_TIMEOUT_SECONDS``).
    This milestone does not require a live-model acceptance test; setting the
    environment variables is enough for a real deployment.
    """

    name = "compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 30,
    ) -> None:
        if not base_url:
            raise EmbeddingConfigError("EMBEDDING_BASE_URL 未配置")
        if not api_key:
            raise EmbeddingConfigError("EMBEDDING_API_KEY 未配置")
        if not model:
            raise EmbeddingConfigError("EMBEDDING_MODEL 未配置")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "OpenAICompatibleEmbeddingProvider":
        return cls(
            base_url=EMBEDDING_BASE_URL,
            api_key=EMBEDDING_API_KEY,
            model=EMBEDDING_MODEL,
            timeout_seconds=EMBEDDING_TIMEOUT_SECONDS,
        )

    def _embeddings_url(self) -> str:
        return self.base_url + "/embeddings"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = {
            "model": self.model,
            "input": texts,
        }
        request = urllib_request.Request(
            self._embeddings_url(),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            raise EmbeddingHTTPError(
                f"Embedding 供应商返回 HTTP {exc.code}"
            ) from None
        except urllib_error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise EmbeddingTimeoutError("Embedding 调用超时") from None
            raise EmbeddingHTTPError(f"无法连接 Embedding 供应商: {reason}") from None
        except TimeoutError:
            raise EmbeddingTimeoutError("Embedding 调用超时") from None
        except (json.JSONDecodeError, KeyError) as exc:
            raise EmbeddingHTTPError(f"Embedding 供应商响应无法解析: {exc}") from None

        try:
            raw_vectors = [item["embedding"] for item in payload["data"]]
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingHTTPError(
                "Embedding 供应商响应缺少 data[].embedding"
            ) from None

        self.validate_batch(texts, raw_vectors)
        return raw_vectors
