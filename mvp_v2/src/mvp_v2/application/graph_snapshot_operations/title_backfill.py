from __future__ import annotations

import re

from pydantic import ValidationError

from mvp_v2.domain.graph import Chunk, Document
from mvp_v2.domain.snapshot import GraphSnapshot

_SHA256_TITLE = re.compile(r"^[0-9a-f]{64}$")


class TitleBackfillError(ValueError):
    pass


def _heading_from(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            candidate = stripped.lstrip("#").strip()
            if candidate:
                return candidate
    return None


def _normalise_title(candidate: str) -> str:
    return " ".join(candidate.split())


def _fallback_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    raise TitleBackfillError("first chunk content is empty")


def _first_chunks_by_version(snapshot: GraphSnapshot) -> dict[str, Chunk]:
    chunk_by_id = {chunk.id: chunk for chunk in snapshot.chunks}
    grouped: dict[str, list[Chunk]] = {}
    for edge in snapshot.has_chunks:
        chunk = chunk_by_id.get(edge.target_id)
        if chunk is not None:
            grouped.setdefault(edge.source_id, []).append(chunk)
    return {
        version_id: min(chunks, key=lambda item: (item.position, item.id))
        for version_id, chunks in grouped.items()
    }


def fill_missing_document_titles(
    snapshot: GraphSnapshot,
) -> tuple[GraphSnapshot, int]:
    current_versions = {
        version.id for version in snapshot.document_versions if version.is_current
    }
    doc_to_version = {
        edge.source_id: edge.target_id
        for edge in snapshot.has_versions
        if edge.target_id in current_versions
    }
    first_chunk_by_version = _first_chunks_by_version(snapshot)

    documents = list(snapshot.documents)
    renamed = 0
    for index, document in enumerate(documents):
        if not document.is_active or not _SHA256_TITLE.fullmatch(document.title):
            continue
        chunk = first_chunk_by_version.get(doc_to_version.get(document.id, ""))
        if chunk is None:
            continue
        title = _normalise_title(
            _heading_from(chunk.content) or _fallback_title(chunk.content)
        )
        payload = document.model_dump()
        payload["title"] = title
        try:
            documents[index] = Document.model_validate(payload)
        except ValidationError:
            continue
        renamed += 1
    if renamed == 0:
        return snapshot, 0
    return snapshot.model_copy(update={"documents": tuple(documents)}), renamed
