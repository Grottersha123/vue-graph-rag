from __future__ import annotations

from dataclasses import dataclass

from mvp_v2.domain.graph import Asset, Document, DocumentVersion
from mvp_v2.domain.snapshot import GraphSnapshot


@dataclass(frozen=True, slots=True)
class DocumentView:
    document: Document
    version: DocumentVersion
    content: str
    chunk_count: int
    assets: tuple[Asset, ...]


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
        edge.target_id
        for edge in snapshot.has_chunks
        if edge.source_id == version.id
    }
    chunks = tuple(
        sorted(
            (item for item in snapshot.chunks if item.id in chunk_ids),
            key=lambda item: (item.position, item.id),
        )
    )
    asset_ids = {
        edge.target_id
        for edge in snapshot.has_assets
        if edge.source_id == version.id
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
