from __future__ import annotations

from mvp_v2.domain.graph import Document
from mvp_v2.domain.snapshot import GraphSnapshot


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
