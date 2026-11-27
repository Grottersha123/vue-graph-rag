from __future__ import annotations

from typing import Protocol


class VisionModel(Protocol):
    def describe(self, *, image: bytes, mime_type: str, model: str) -> str: ...


class EmbeddingModel(Protocol):
    def embed(
        self, *, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, ...], ...]: ...


class TextGenerationModel(Protocol):
    def generate(self, *, prompt: str, model: str) -> str: ...
