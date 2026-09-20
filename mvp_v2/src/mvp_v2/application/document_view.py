from __future__ import annotations

from dataclasses import dataclass

from mvp_v2.domain.graph import Asset, Document, DocumentVersion
from mvp_v2.domain.snapshot import GraphSnapshot


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    document: Document
    version: DocumentVersion
    chunk_count: int
    asset_count: int


@dataclass(frozen=True, slots=True)
class DocumentView:
    document: Document
    version: DocumentVersion
    content: str
    chunk_count: int
    assets: tuple[Asset, ...]


def build_document_summaries(snapshot: GraphSnapshot) -> tuple[DocumentSummary, ...]:
    current_versions = {
        version.id: version
        for version in snapshot.document_versions
        if version.is_current
    }
    version_id_by_document: dict[str, str] = {}
    for version_edge in snapshot.has_versions:
        if version_edge.target_id not in current_versions:
            continue
        if version_edge.source_id in version_id_by_document:
            raise ValueError("document must have exactly one current version")
        version_id_by_document[version_edge.source_id] = version_edge.target_id
    chunk_counts: dict[str, int] = {}
    for chunk_edge in snapshot.has_chunks:
        chunk_counts[chunk_edge.source_id] = (
            chunk_counts.get(chunk_edge.source_id, 0) + 1
        )
    asset_counts: dict[str, int] = {}
    for asset_edge in snapshot.has_assets:
        asset_counts[asset_edge.source_id] = (
            asset_counts.get(asset_edge.source_id, 0) + 1
        )

    summaries: list[DocumentSummary] = []
    for document in snapshot.documents:
        version = current_versions.get(version_id_by_document.get(document.id, ""))
        if version is None:
            raise ValueError("document must have exactly one current version")
        summaries.append(
            DocumentSummary(
                document=document,
                version=version,
                chunk_count=chunk_counts.get(version.id, 0),
                asset_count=asset_counts.get(version.id, 0),
            )
        )
    return tuple(summaries)


def build_document_view(snapshot: GraphSnapshot, document_id: str) -> DocumentView:
    document = next(
        (item for item in snapshot.documents if item.id == document_id), None
    )
    if document is None:
        raise ValueError("document does not exist")
    version_ids = {
        edge.target_id
        for edge in snapshot.has_versions
        if edge.source_id == document_id
    }
    current_versions = tuple(
        item
        for item in snapshot.document_versions
        if item.id in version_ids and item.is_current
    )
    if len(current_versions) != 1:
        raise ValueError("document must have exactly one current version")
    version = current_versions[0]
    chunk_ids = {
        edge.target_id for edge in snapshot.has_chunks if edge.source_id == version.id
    }
    chunks = tuple(
        sorted(
            (item for item in snapshot.chunks if item.id in chunk_ids),
            key=lambda item: (item.position, item.id),
        )
    )
    asset_ids = {
        edge.target_id for edge in snapshot.has_assets if edge.source_id == version.id
    }
    assets = tuple(
        sorted(
            (item for item in snapshot.assets if item.id in asset_ids),
            key=lambda item: (item.position, item.id),
        )
    )
    return DocumentView(
        document=document,
        version=version,
        content="\n\n".join(item.content for item in chunks),
        chunk_count=len(chunks),
        assets=assets,
    )
