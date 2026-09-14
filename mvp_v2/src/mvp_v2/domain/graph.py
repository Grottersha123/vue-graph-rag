from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from mvp_v2.domain.base import (
    AliasedContractModel,
    AllowEmptyMultilineText,
    AllowEmptyText,
    ContractModel,
    EntityId,
    FiniteVector,
    NonBlankText,
    OptionalRelativePath,
    RelativePath,
    RequiredMultilineText,
    Sha256,
    UtcDateTime,
)

NonNegativeInt = Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=9_223_372_036_854_775_807)]


class SourceType(ContractModel):
    id: EntityId
    code: NonBlankText


class Document(ContractModel):
    id: EntityId
    title: NonBlankText
    source_name: AllowEmptyText
    content_format: NonBlankText
    source_url: AllowEmptyText
    external_id: AllowEmptyText
    is_active: bool


class DocumentVersion(ContractModel):
    id: EntityId
    version: PositiveInt
    sha256: str
    local_path: OptionalRelativePath
    created_at: UtcDateTime
    is_current: bool
    error: AllowEmptyMultilineText

    @model_validator(mode="after")
    def validate_sha_state(self) -> DocumentVersion:
        if self.sha256 and (
            len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("SHA-256 must contain 64 lowercase hexadecimal characters")
        if not self.sha256 and (
            self.is_current or not self.error or self.error.isspace()
        ):
            raise ValueError("empty SHA-256 requires a non-current errored version")
        return self


class Chunk(ContractModel):
    id: EntityId
    position: NonNegativeInt
    content: RequiredMultilineText
    content_hash: Sha256

    @model_validator(mode="after")
    def validate_content_hash(self) -> Chunk:
        expected = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_hash != expected:
            raise ValueError("content_hash must equal the SHA-256 of UTF-8 content")
        return self


class Asset(ContractModel):
    id: EntityId
    asset_type: Literal["image"]
    mime_type: NonBlankText
    source_url: AllowEmptyText
    local_path: RelativePath
    external_id: AllowEmptyText
    sha256: Sha256
    position: NonNegativeInt
    caption: AllowEmptyMultilineText
    llm_description: AllowEmptyMultilineText
    ocr_text: AllowEmptyMultilineText


class Embedding(ContractModel):
    id: EntityId
    provider: NonBlankText
    model: NonBlankText
    model_version: NonBlankText
    purpose: Literal["chunk_retrieval", "asset_description_retrieval"]
    dimension: PositiveInt
    vector: FiniteVector
    created_at: UtcDateTime
    is_current: bool

    @model_validator(mode="after")
    def validate_vector_dimension(self) -> Embedding:
        if len(self.vector) != self.dimension:
            raise ValueError("vector length must equal dimension")
        return self


class Role(ContractModel):
    id: EntityId
    name: NonBlankText


class Process(ContractModel):
    id: EntityId
    name: NonBlankText
    status: NonBlankText
    owner: AllowEmptyText


class Status(ContractModel):
    id: EntityId
    name: NonBlankText
    description: AllowEmptyMultilineText


class Relation(AliasedContractModel):
    source_id: EntityId = Field(serialization_alias="from", validation_alias="from")
    target_id: EntityId = Field(serialization_alias="to", validation_alias="to")


class HasSourceType(Relation):
    pass


class HasVersion(Relation):
    pass


class Supersedes(Relation):
    pass


class HasStatus(Relation):
    pass


class HasChunk(Relation):
    pass


class HasAsset(Relation):
    pass


class ContainsAsset(Relation):
    pass


class HasEmbeddingChunk(Relation):
    pass


class HasEmbeddingAsset(Relation):
    pass


class LinksTo(Relation):
    pass


class Describes(Relation):
    pass


class ForRole(Relation):
    pass
