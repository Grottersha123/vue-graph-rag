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


CustomPropertyValue = str | int | bool | FiniteVector


class CustomProperty(ContractModel):
    name: NonBlankText
    value: CustomPropertyValue


class ExtensibleNode(ContractModel):
    custom_properties: tuple[CustomProperty, ...] = ()

    @model_validator(mode="after")
    def validate_custom_property_names(self) -> ExtensibleNode:
        names = tuple(item.name for item in self.custom_properties)
        if len(names) != len(set(names)):
            raise ValueError("custom property names must be unique")
        return self


class SourceType(ExtensibleNode):
    id: EntityId
    code: NonBlankText


class Document(ExtensibleNode):
    id: EntityId
    title: NonBlankText
    source_name: AllowEmptyText
    content_format: NonBlankText
    source_url: AllowEmptyText
    external_id: AllowEmptyText
    is_active: bool
    summary: AllowEmptyMultilineText = ""


class DocumentVersion(ExtensibleNode):
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


class Chunk(ExtensibleNode):
    id: EntityId
    position: NonNegativeInt
    content: RequiredMultilineText
    content_hash: Sha256
    summary: AllowEmptyMultilineText = ""

    @model_validator(mode="after")
    def validate_content_hash(self) -> Chunk:
        expected = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_hash != expected:
            raise ValueError("content_hash must equal the SHA-256 of UTF-8 content")
        return self


class Asset(ExtensibleNode):
    id: EntityId
    asset_type: Literal["image"]
    mime_type: NonBlankText
    source_url: AllowEmptyText
    local_path: RelativePath
    external_id: AllowEmptyText
    sha256: Sha256
    position: NonNegativeInt
    description: AllowEmptyMultilineText = ""
    ocr_text: AllowEmptyMultilineText = ""


class Embedding(ExtensibleNode):
    id: EntityId
    provider: NonBlankText
    model: NonBlankText
    model_version: NonBlankText
    dimension: PositiveInt
    vector: FiniteVector
    created_at: UtcDateTime
    is_current: bool
    summary_vector: FiniteVector = ()

    @model_validator(mode="after")
    def validate_vector_dimension(self) -> Embedding:
        if len(self.vector) != self.dimension:
            raise ValueError("vector length must equal dimension")
        if self.summary_vector and len(self.summary_vector) != self.dimension:
            raise ValueError("summary_vector must be empty or match dimension")
        return self


class Role(ExtensibleNode):
    id: EntityId
    name: NonBlankText


class Process(ExtensibleNode):
    id: EntityId
    name: NonBlankText
    status: NonBlankText
    owner: AllowEmptyText


class Status(ExtensibleNode):
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


class HasSummaryEmbedding(Relation):
    pass


class LinksTo(Relation):
    pass


class Describes(Relation):
    pass


class ForRole(Relation):
    pass
