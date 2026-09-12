"""Data contracts and CSV layouts for the MVP graph."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

DOCUMENT_COLUMNS = [
    "id",
    "title",
    "source_url",
    "external_id",
    "source_type",
    "sha256",
    "local_path",
    "content_format",
    "source_format",
    "source_name",
    "is_active",
    "status",
]
VERSION_COLUMNS = ["id", "version", "sha256", "local_path", "created_at", "is_current", "error"]
ASSET_COLUMNS = [
    "id",
    "asset_type",
    "mime_type",
    "source_url",
    "local_path",
    "external_id",
    "sha256",
    "position",
    "caption",
    "ocr_text",
]
MANUAL_LINK_COLUMNS = [
    "source_document_id",
    "target_document_id",
    "target_external_id",
    "source_url",
    "target_url",
    "link_type",
    "discovered_depth",
    "reason",
]
ROLE_COLUMNS = ["id", "name"]
STATUS_COLUMNS = ["id", "name", "description"]
FOR_ROLE_COLUMNS = ["from", "to"]

MANIFEST_COLUMNS = [
    "asset_id",
    "source_document_id",
    "source_url",
    "local_path",
    "mime_type",
    "sha256",
    "status",
    "reason",
]


class DocumentRecord(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str
    title: str
    source_url: str
    external_id: str
    source_type: str
    sha256: str
    local_path: str
    content_format: str = "markdown"
    source_format: str = "markdown"
    source_name: str = ""
    is_active: bool = True
    status: str = "downloaded"
    raw_local_path: str = Field(default="", exclude=True)


class RoleRecord(BaseModel):
    id: str
    name: str


class StatusRecord(BaseModel):
    id: str
    name: str
    description: str = ""


class DocumentVersionRecord(BaseModel):
    id: str
    version: int = Field(ge=1)
    sha256: str
    local_path: str
    created_at: str
    is_current: bool = True
    error: str = ""


class AssetRecord(BaseModel):
    id: str
    asset_type: str = "image"
    mime_type: str
    source_url: str
    local_path: str
    external_id: str
    sha256: str
    position: int = Field(ge=0)
    caption: str = ""
    ocr_text: str = ""


class ManualLinkRecord(BaseModel):
    source_document_id: str
    target_document_id: str
    target_external_id: str
    source_url: str
    target_url: str
    link_type: str = "LINKS_TO"
    discovered_depth: int = Field(ge=0)
    reason: str


class ImageManifestRecord(BaseModel):
    asset_id: str
    source_document_id: str
    source_url: str
    local_path: str = ""
    mime_type: str = ""
    sha256: str = ""
    status: str
    reason: str = ""


@dataclass(frozen=True)
class ImageCandidate:
    source_url: str
    position: int
    caption: str = ""
    content: bytes | None = None
    mime_type: str = ""
    external_id: str = ""


@dataclass
class ParsedDocument:
    title: str
    content_format: str
    links: list[str] = field(default_factory=list)
    images: list[ImageCandidate] = field(default_factory=list)


@dataclass
class DownloadedDocument:
    content: bytes
    content_format: str
    mime_type: str
    final_url: str
    title: str = ""


NODE_MODELS: dict[str, type[BaseModel]] = {
    "Document": DocumentRecord,
    "DocumentVersion": DocumentVersionRecord,
    "Status": StatusRecord,
    "Asset": AssetRecord,
    "Role": RoleRecord,
}
NODE_COLUMNS: dict[str, tuple[str, ...]] = {
    table: tuple(
        field_name
        for field_name, field_info in model.model_fields.items()
        if not field_info.exclude
    )
    for table, model in NODE_MODELS.items()
}


NODE_TABLES = {
    "SourceType": "CREATE NODE TABLE SourceType (id STRING PRIMARY KEY, code STRING)",  # noqa: E501
    "Document": "CREATE NODE TABLE Document (id STRING PRIMARY KEY, title STRING, source_url STRING, external_id STRING, source_type STRING, sha256 STRING, local_path STRING, content_format STRING, source_format STRING, source_name STRING, is_active BOOLEAN, status STRING)",  # noqa: E501
    "DocumentVersion": "CREATE NODE TABLE DocumentVersion (id STRING PRIMARY KEY, version INT64, sha256 STRING, local_path STRING, created_at TIMESTAMP, is_current BOOLEAN, error STRING)",  # noqa: E501
    "Status": "CREATE NODE TABLE Status (id STRING PRIMARY KEY, name STRING, description STRING)",
    "Asset": "CREATE NODE TABLE Asset (id STRING PRIMARY KEY, asset_type STRING, mime_type STRING, source_url STRING, local_path STRING, external_id STRING, sha256 STRING, position INT64, caption STRING, ocr_text STRING)",  # noqa: E501
    "Role": "CREATE NODE TABLE Role (id STRING PRIMARY KEY, name STRING)",
}
REL_TABLES = {
    "HAS_DOCUMENT": "CREATE REL TABLE HAS_DOCUMENT (FROM SourceType TO Document)",
    "HAS_VERSION": "CREATE REL TABLE HAS_VERSION (FROM Document TO DocumentVersion)",  # noqa: E501
    "HAS_STATUS": "CREATE REL TABLE HAS_STATUS (FROM DocumentVersion TO Status)",
    "HAS_ERROR": "CREATE REL TABLE HAS_ERROR (FROM Document TO Status)",
    "LINKS_TO": "CREATE REL TABLE LINKS_TO (FROM Document TO Document)",
    "HAS_ASSET": "CREATE REL TABLE HAS_ASSET (FROM DocumentVersion TO Asset)",
    "FOR_ROLE": "CREATE REL TABLE FOR_ROLE (FROM Document TO Role)",
}
CSV_TABLES = (
    "SourceType",
    "Document",
    "DocumentVersion",
    "Status",
    "Asset",
    "Role",
    "HAS_DOCUMENT",
    "HAS_VERSION",
    "HAS_STATUS",
    "HAS_ERROR",
    "LINKS_TO",
    "HAS_ASSET",
    "FOR_ROLE",
)
