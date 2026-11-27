from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

FallbackReason = Literal[
    "timeout", "transport_error", "provider_error", "invalid_response"
]


class ProviderGenerationError(RuntimeError):
    def __init__(self, reason: FallbackReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationResult:
    text: str
    provider: str
    fallback_used: bool = False
    fallback_reason: FallbackReason | None = None
    request_id: str | None = None


class VisionModel(Protocol):
    def describe(self, *, image: bytes, mime_type: str, model: str) -> str: ...


class EmbeddingModel(Protocol):
    def embed(
        self, *, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, ...], ...]: ...


class TextGenerationModel(Protocol):
    def generate(self, *, prompt: str, model: str) -> str: ...


class GenerationProvider(Protocol):
    def generate(self, *, prompt: str, model: str) -> GenerationResult: ...
