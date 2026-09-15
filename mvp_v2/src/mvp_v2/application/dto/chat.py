from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from mvp_v2.application.dto.retrieval import Evidence, SearchRequest
from mvp_v2.application.ingestion_contracts import ModelAuthorization
from mvp_v2.domain.base import ContractModel, EntityId, NonBlankText


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
    document_id: EntityId
    document_title: NonBlankText
    version_id: EntityId
    version: Annotated[int, Field(strict=True, ge=1)]
    source_url: str
    local_path: str


class ChatAnswer(ContractModel):
    answer: NonBlankText
    citations: tuple[Citation, ...]
    evidence: tuple[Evidence, ...]
    generated: bool
