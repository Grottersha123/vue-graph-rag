from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, cast

import httpx

from vue_rag.ports.models import ProviderGenerationError


class SeloraError(ProviderGenerationError):
    pass


class SeloraTransport(Protocol):
    def post(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class HttpxSeloraTransport:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        max_body_bytes: int = 1_000_000,
    ) -> None:
        if not api_key:
            raise ValueError("Selora API key must not be empty")
        if timeout_seconds <= 0 or max_body_bytes < 1:
            raise ValueError("Selora transport limits must be positive")
        self._endpoint = f"{base_url.rstrip('/')}/v1/chat/completions"
        self._authorization = f"Bearer {api_key}"
        self._timeout = timeout_seconds
        self._max_body_bytes = max_body_bytes

    def post(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                with client.stream(
                    "POST",
                    self._endpoint,
                    json=dict(payload),
                    headers={"Authorization": self._authorization},
                ) as response:
                    if not response.is_success:
                        raise SeloraError(
                            "provider_error",
                            f"Selora returned HTTP {response.status_code}",
                        )
                    body = self._read_bounded(response)
        except SeloraError:
            raise
        except httpx.TimeoutException as error:
            raise SeloraError("timeout", "Selora request timed out") from error
        except httpx.RequestError as error:
            raise SeloraError("transport_error", "Selora connection failed") from error
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SeloraError(
                "invalid_response", "Selora returned invalid JSON"
            ) from error
        if not isinstance(parsed, dict):
            raise SeloraError(
                "invalid_response", "Selora returned an invalid JSON object"
            )
        return cast(dict[str, object], parsed)

    def _read_bounded(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self._max_body_bytes:
                raise SeloraError(
                    "invalid_response", "Selora response exceeds configured limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)


class SeloraAdapter:
    def __init__(self, transport: SeloraTransport, model: str) -> None:
        if not model.strip():
            raise ValueError("Selora model must not be empty")
        self._transport = transport
        self._model = model

    def generate(self, *, prompt: str, model: str) -> str:
        del model
        response = self._transport.post(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        )
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise SeloraError("invalid_response", "Selora response has no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise SeloraError("invalid_response", "Selora response choice is invalid")
        message = first.get("message")
        if not isinstance(message, dict):
            raise SeloraError("invalid_response", "Selora response message is invalid")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise SeloraError("invalid_response", "Selora response content is empty")
        return content
