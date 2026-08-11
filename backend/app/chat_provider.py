"""Chat provider abstraction (W3-A).

The rest of the application only depends on the ``ChatProvider`` interface —
never on a vendor name, a provider URL, an API key or a concrete model id. The
worker calls ``extract_structured`` to get a validated JSON object; the fake
provider returns a deterministic result, the ``compatible`` provider speaks
the OpenAI-compatible chat-completions protocol and is configured entirely
from environment variables.

Business code must treat the raw model response as untrusted data: it only
ever reaches the database after passing Pydantic validation.
"""

import json
from abc import ABC, abstractmethod
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel

from .config import (
    CHAT_API_KEY,
    CHAT_BASE_URL,
    CHAT_MODEL,
    CHAT_TIMEOUT_SECONDS,
)


class ChatProviderError(Exception):
    """Base class for every chat-provider failure.

    The task layer treats it as a retryable failure: the worker increments
    ``attempts`` and the RQ retry policy decides whether to try again.
    """


class ProviderTimeoutError(ChatProviderError):
    """The provider did not answer within ``CHAT_TIMEOUT_SECONDS``."""


class ProviderHTTPError(ChatProviderError):
    """The provider returned a non-200 HTTP response."""


class ProviderConfigError(ChatProviderError):
    """The provider is not configured (missing base URL / API key / model)."""


class StructuredOutputError(ChatProviderError):
    """The model answer is not valid JSON or does not match the schema.

    This is a *validation* failure, so the raw model output must never be
    written to the database.
    """


class ChatProvider(ABC):
    """Port for chat-style LLM access used by the AI analysis worker."""

    name: str = "abstract"

    @abstractmethod
    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> dict[str, Any]:
        """Ask the model to answer as one JSON object matching ``schema``.

        Returns the validated model output as a plain dict. Implementations
        must raise ``StructuredOutputError`` when the answer is missing,
        is not valid JSON, or fails Pydantic validation — never return
        unvalidated data.
        """

    @classmethod
    def parse_structured(
        cls, raw: str, schema: type[BaseModel]
    ) -> dict[str, Any]:
        """Parse and validate one raw model answer against ``schema``.

        Shared by every implementation so JSON parsing and Pydantic
        validation behave identically for fake and real providers.
        """
        if raw is None or not str(raw).strip():
            raise StructuredOutputError("模型没有返回内容")
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise StructuredOutputError(f"模型返回的不是合法 JSON: {exc}") from None
        try:
            return schema.model_validate(obj).model_dump()
        except Exception as exc:
            raise StructuredOutputError(f"模型输出未通过 schema 校验: {exc}") from None


class FakeChatProvider(ChatProvider):
    """Deterministic, offline provider used by tests and local development.

    It returns a fixed structured analysis and can be told to simulate a
    timeout or a malformed/unparsable answer so failure paths can be tested
    without touching the network. ``failures_remaining`` decrements on every
    call until it reaches zero, so a test can make the next N calls fail and
    then succeed — exactly what the RQ retry path needs.
    """

    name = "fake"

    def __init__(
        self,
        *,
        fail_with: ChatProviderError | None = None,
        failures_remaining: int = 0,
        raw_output: str | None = None,
    ) -> None:
        self.fail_with = fail_with
        self.failures_remaining = failures_remaining
        self.raw_output = raw_output

    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> dict[str, Any]:
        if self.fail_with is not None and self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise self.fail_with
        raw = self.raw_output
        if raw is None:
            raw = (
                '{"category": "technical", "summary": "用户无法登录系统", '
                '"priority": "high", "sentiment": "negative", '
                '"confidence": 0.91, '
                '"reason": "问题影响核心功能"}'
            )
        return self.parse_structured(raw, schema)


class OpenAICompatibleChatProvider(ChatProvider):
    """Real provider speaking the OpenAI-compatible chat completions API.

    Configured exclusively from the environment (``CHAT_BASE_URL``,
    ``CHAT_API_KEY``, ``CHAT_MODEL``, ``CHAT_TIMEOUT_SECONDS``). Never hard-code
    a vendor URL, key or model id in business code. This milestone does not
    require a live-model acceptance test; the class is present so a real
    deployment only has to set environment variables.
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
            raise ProviderConfigError("CHAT_BASE_URL 未配置")
        if not api_key:
            raise ProviderConfigError("CHAT_API_KEY 未配置")
        if not model:
            raise ProviderConfigError("CHAT_MODEL 未配置")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "OpenAICompatibleChatProvider":
        return cls(
            base_url=CHAT_BASE_URL,
            api_key=CHAT_API_KEY,
            model=CHAT_MODEL,
            timeout_seconds=CHAT_TIMEOUT_SECONDS,
        )

    def _chat_url(self) -> str:
        # Both https://host/v1 and https://host (the app may already include
        # /v1) are accepted by appending /chat/completions.
        return self.base_url + "/chat/completions"

    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib_request.Request(
            self._chat_url(),
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
            raise ProviderHTTPError(
                f"供应商返回 HTTP {exc.code}"
            ) from None
        except urllib_error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise ProviderTimeoutError("AI 调用超时") from None
            raise ProviderHTTPError(f"无法连接 AI 供应商: {reason}") from None
        except TimeoutError:
            raise ProviderTimeoutError("AI 调用超时") from None
        except (json.JSONDecodeError, KeyError) as exc:
            raise ProviderHTTPError(f"供应商响应无法解析: {exc}") from None

        raw = None
        try:
            raw = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderHTTPError("供应商响应缺少 choices[0].message.content") from None

        return self.parse_structured(raw, schema)
