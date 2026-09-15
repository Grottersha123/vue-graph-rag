from __future__ import annotations

import base64
import json
from typing import Protocol, cast
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    pass


class JsonTransport(Protocol):
    def post(self, path: str, payload: bytes) -> bytes: ...


class LocalHttpTransport:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_body_bytes: int = 4_000_000,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Ollama URL must be local HTTP")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_body = max_body_bytes

    def post(self, path: str, payload: bytes) -> bytes:
        request = Request(
            self._base_url + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - URL is constructor-validated local HTTP
                body = cast(bytes, response.read(self._max_body + 1))
        except OSError as error:
            raise OllamaError("local Ollama request failed") from error
        if len(body) > self._max_body:
            raise OllamaError("local Ollama response exceeds configured limit")
        return body


class OllamaAdapter:
    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def describe(self, *, image: bytes, mime_type: str, model: str) -> str:
        del mime_type
        payload = {
            "model": model,
            "prompt": "Describe this image for factual retrieval.",
            "images": [base64.b64encode(image).decode("ascii")],
            "stream": False,
        }
        result = self._object("/api/generate", payload)
        response = result.get("response")
        if not isinstance(response, str):
            raise OllamaError("invalid Ollama generation response")
        return response

    def generate(self, *, prompt: str, model: str) -> str:
        result = self._object(
            "/api/generate", {"model": model, "prompt": prompt, "stream": False}
        )
        response = result.get("response")
        if not isinstance(response, str):
            raise OllamaError("invalid Ollama generation response")
        return response

    def embed(
        self, *, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, ...], ...]:
        result = self._object("/api/embed", {"model": model, "input": texts})
        raw = result.get("embeddings")
        if not isinstance(raw, list):
            raise OllamaError("invalid Ollama embedding response")
        try:
            return tuple(
                tuple(_number(value) for value in cast(list[object], vector))
                for vector in raw
                if isinstance(vector, list)
            )
        except (TypeError, ValueError) as error:
            raise OllamaError("invalid Ollama embedding response") from error

    def _object(self, path: str, payload: object) -> dict[str, object]:
        try:
            value = json.loads(
                self._transport.post(path, json.dumps(payload).encode("utf-8"))
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise OllamaError("invalid JSON from local Ollama") from error
        if not isinstance(value, dict):
            raise OllamaError("invalid JSON object from local Ollama")
        return cast(dict[str, object], value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("embedding component is not numeric")
    return float(value)
