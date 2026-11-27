from __future__ import annotations

from typing import Protocol

from vue_rag.application.ingestion_contracts import ExtractedInput, IngestionRequest


class InputExtractor(Protocol):
    def extract(self, request: IngestionRequest) -> ExtractedInput: ...


class RuntimeFileStore(Protocol):
    def write_files(self, files: tuple[tuple[str, bytes], ...]) -> tuple[str, ...]: ...


class UrlFetcher(Protocol):
    def fetch(self, url: str) -> tuple[bytes, str]: ...
