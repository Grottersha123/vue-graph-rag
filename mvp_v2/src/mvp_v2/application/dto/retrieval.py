from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from mvp_v2.domain.base import ContractModel, EntityId, FiniteVector, NonBlankText

PositiveInt = Annotated[int, Field(strict=True, ge=1, le=9_223_372_036_854_775_807)]


class SubjectKind(StrEnum):
    CHUNK = "chunk"
    ASSET = "asset"


class EmbeddingPurpose(StrEnum):
    CHUNK_RETRIEVAL = "chunk_retrieval"
    ASSET_DESCRIPTION_RETRIEVAL = "asset_description_retrieval"


def _subject_kind(value: object) -> SubjectKind:
    if isinstance(value, SubjectKind):
        return value
    if type(value) is str:
        try:
            return SubjectKind(value)
        except ValueError as error:
            raise ValueError("unknown subject kind") from error
    raise ValueError("subject kind must be an exact string value")


def _embedding_purpose(value: object) -> EmbeddingPurpose:
    if isinstance(value, EmbeddingPurpose):
        return value
    if type(value) is str:
        try:
            return EmbeddingPurpose(value)
        except ValueError as error:
            raise ValueError("unknown embedding purpose") from error
    raise ValueError("embedding purpose must be an exact string value")


SubjectKindValue = Annotated[SubjectKind, BeforeValidator(_subject_kind)]
EmbeddingPurposeValue = Annotated[EmbeddingPurpose, BeforeValidator(_embedding_purpose)]


class SubjectRef(ContractModel):
    kind: SubjectKindValue
    id: EntityId


class EmbeddingSpaceIdentity(ContractModel):
    provider: NonBlankText
    model: NonBlankText
    model_version: NonBlankText
    dimension: PositiveInt


class VectorQuery(ContractModel):
    space: EmbeddingSpaceIdentity
    vector: FiniteVector
    purposes: tuple[EmbeddingPurposeValue, ...]
    limit: PositiveInt

    @model_validator(mode="after")
    def validate_query(self) -> VectorQuery:
        if len(self.vector) != self.space.dimension:
            raise ValueError("query vector length must equal embedding-space dimension")
        if not self.purposes:
            raise ValueError("at least one retrieval purpose is required")
        return self


class VectorHit(ContractModel):
    subject: SubjectRef
    embedding_id: EntityId
    purpose: EmbeddingPurposeValue
    score: Annotated[float, Field(strict=True, allow_inf_nan=False)]


class EmbeddingProviderOutput(ContractModel):
    space: EmbeddingSpaceIdentity
    vector: FiniteVector

    @model_validator(mode="after")
    def validate_dimension(self) -> EmbeddingProviderOutput:
        if len(self.vector) != self.space.dimension:
            raise ValueError(
                "provider vector length must equal embedding-space dimension"
            )
        return self


class EmbeddingBuildRequest(ContractModel):
    subjects: tuple[SubjectRef, ...]
    space: EmbeddingSpaceIdentity
    rebuild_all: bool

    @model_validator(mode="after")
    def validate_subjects(self) -> EmbeddingBuildRequest:
        if not self.subjects:
            raise ValueError("embedding build requires at least one subject")
        return self
