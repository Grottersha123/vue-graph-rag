from __future__ import annotations

from dataclasses import dataclass

from mvp_v2.domain.graph import Document
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.snapshot import GraphSnapshot


@dataclass(frozen=True, slots=True)
class DocumentMetadataUpdate:
    document_id: str
    title: str
    source_url: str
    external_id: str
    is_active: bool


def rename_document(
    snapshot: GraphSnapshot, document_id: str, title: str
) -> GraphSnapshot:
    normalized_title = " ".join(title.split())
    if not normalized_title:
        raise ValueError("document title must not be blank")

    documents = list(snapshot.documents)
    for index, document in enumerate(documents):
        if document.id != document_id:
            continue
        if document.title == normalized_title:
            return snapshot
        payload = document.model_dump()
        payload["title"] = normalized_title
        documents[index] = Document.model_validate(payload)
        return snapshot.model_copy(update={"documents": tuple(documents)})
    raise ValueError("document does not exist")


def update_document_metadata(
    snapshot: GraphSnapshot,
    updates: tuple[DocumentMetadataUpdate, ...],
) -> GraphSnapshot:
    update_by_id = {item.document_id: item for item in updates}
    document_ids = {item.id for item in snapshot.documents}
    if len(update_by_id) != len(updates) or set(update_by_id) != document_ids:
        raise ValueError("metadata updates must match every document exactly once")

    documents: list[Document] = []
    for document in snapshot.documents:
        update = update_by_id[document.id]
        title = " ".join(update.title.split())
        if not title:
            raise ValueError("document title must not be blank")
        payload = document.model_dump()
        payload.update(
            title=title,
            source_url=update.source_url.strip(),
            external_id=update.external_id.strip(),
            is_active=update.is_active,
        )
        documents.append(Document.model_validate(payload))

    external_ids = [item.external_id for item in documents if item.external_id]
    if len(external_ids) != len(set(external_ids)):
        raise ValueError("non-empty external_id values must be unique")
    updated_documents = tuple(documents)
    if updated_documents == snapshot.documents:
        return snapshot
    candidate = snapshot.model_copy(update={"documents": updated_documents})
    if not validate_graph(candidate).is_valid:
        raise ValueError("metadata changes violate graph invariants")
    return candidate
