from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from vue_rag.application.dto.retrieval import ContextScope, Evidence, SearchRequest
from vue_rag.application.ingestion_contracts import ModelAuthorization
from vue_rag.domain.base import (
    ContractModel,
    EntityId,
    NonBlankText,
    RequiredMultilineText,
)
from vue_rag.ports.models import FallbackReason


class ChatRequest(ContractModel):
    question: NonBlankText
    search: SearchRequest
    model: NonBlankText
    model_version: NonBlankText
    authorization: ModelAuthorization

    @model_validator(mode="after")
    def questions_match(self) -> ChatRequest:
        if self.question != self.search.question:
            raise ValueError("chat and search questions must match")
        return self


class Citation(ContractModel):
    number: Annotated[int, Field(strict=True, ge=1, le=100)]
    evidence_id: EntityId
    score: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    matched_chunk_position: Annotated[int, Field(strict=True, ge=0)] | None
    document_chunk_count: Annotated[int, Field(strict=True, ge=0)]
    context_scope: ContextScope
    document_id: EntityId
    document_title: NonBlankText
    version_id: EntityId
    version: Annotated[int, Field(strict=True, ge=1)]
    source_url: str
    local_path: str


class ChatAnswer(ContractModel):
    answer: RequiredMultilineText
    citations: tuple[Citation, ...]
    evidence: tuple[Evidence, ...]
    generated: bool
    provider: str | None = None
    fallback_used: bool = False
    fallback_reason: FallbackReason | None = None

    @model_validator(mode="after")
    def provider_status_is_consistent(self) -> ChatAnswer:
        if self.generated and self.provider is None:
            raise ValueError("generated answers require provider identity")
        if self.fallback_used != (self.fallback_reason is not None):
            raise ValueError("fallback status and reason must be consistent")
        return self
