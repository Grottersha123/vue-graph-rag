from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from mvp_v2.domain.base import (
    AllowEmptyMultilineText,
    AllowEmptyText,
    ContractModel,
    EntityId,
    NonBlankText,
    RelativePath,
    RequiredMultilineText,
    Sha256,
    UtcDateTime,
)
from mvp_v2.domain.graph import PositiveInt
from mvp_v2.domain.snapshot import GraphSnapshot

SourceKind = Literal["url", "pdf", "markdown", "html", "image_collection"]
BatchSize = Annotated[int, Field(strict=True, ge=1, le=256)]


class IngestionRequest(ContractModel):
    kind: SourceKind
    title: AllowEmptyText
    source_type_id: EntityId
    external_id: AllowEmptyText
    status_id: EntityId
    source_name: AllowEmptyText = ""
    source_url: AllowEmptyText = ""
    input_path: AllowEmptyText = ""
    process_id: EntityId | None = None
    role_id: EntityId | None = None


class ExtractedAsset(ContractModel):
    name: RelativePath
    content: bytes
    mime_type: NonBlankText
    caption: AllowEmptyMultilineText = ""
    ocr_text: AllowEmptyMultilineText = ""
    external_id: AllowEmptyText = ""


class ExtractedInput(ContractModel):
    content: bytes
    text: AllowEmptyMultilineText
    extension: NonBlankText
    assets: tuple[ExtractedAsset, ...] = ()
    downloadable_urls: tuple[NonBlankText, ...] = ()


class IngestionPreview(ContractModel):
    request: IngestionRequest
    extracted: ExtractedInput
    document_id: EntityId
    version: PositiveInt
    relative_directory: RelativePath
    content_sha256: Sha256
    chunk_texts: tuple[RequiredMultilineText, ...]


class CommitResult(ContractModel):
    snapshot: GraphSnapshot
    written_paths: tuple[RelativePath, ...]


class ModelAuthorization(ContractModel):
    allow: bool
    operation: Literal["vision_description", "embedding", "generation"]


class ModelIdentity(ContractModel):
    provider: Literal["ollama"]
    model: NonBlankText
    model_version: NonBlankText
    dimension: PositiveInt


class EmbeddingRequest(ContractModel):
    identity: ModelIdentity
    authorization: ModelAuthorization
    mode: Literal["missing", "all"]
    generation_token: NonBlankText
    created_at: UtcDateTime
    batch_size: BatchSize = 16
