from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import PurePosixPath

from mvp_v2.application.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkPolicy,
    make_chunks,
    split_text,
)
from mvp_v2.application.ingestion_contracts import (
    CommitResult,
    IngestionPreview,
    IngestionRequest,
)
from mvp_v2.domain import graph
from mvp_v2.domain.base import stable_id
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.ingestion import InputExtractor, RuntimeFileStore


class IngestionError(ValueError):
    pass


def _markdown_h1(text: str) -> str | None:
    fence_character = ""
    fence_length = 0
    in_comment = False
    previous_line = ""
    for raw_line in text.splitlines():
        if fence_character:
            closing = re.fullmatch(r" {0,3}(`{3,}|~{3,})[ \t]*", raw_line)
            if (
                closing is not None
                and closing.group(1)[0] == fence_character
                and len(closing.group(1)) >= fence_length
            ):
                fence_character = ""
                fence_length = 0
            previous_line = ""
            continue

        started_in_comment = in_comment
        visible_parts: list[str] = []
        remainder = raw_line
        while remainder:
            if in_comment:
                closing_index = remainder.find("-->")
                if closing_index < 0:
                    remainder = ""
                    continue
                in_comment = False
                remainder = remainder[closing_index + 3 :]
                continue
            opening_index = remainder.find("<!--")
            if opening_index < 0:
                visible_parts.append(remainder)
                break
            visible_parts.append(remainder[:opening_index])
            remainder = remainder[opening_index + 4 :]
            in_comment = True
        visible_line = "".join(visible_parts)

        opening = (
            None if started_in_comment else re.match(r" {0,3}(`{3,}|~{3,})", raw_line)
        )
        if opening is not None:
            marker = opening.group(1)
            info = raw_line[opening.end() :]
            if marker[0] != "`" or "`" not in info:
                fence_character = marker[0]
                fence_length = len(marker)
                previous_line = ""
                continue
        if raw_line.startswith(("    ", "\t")):
            previous_line = ""
            continue

        setext = (
            None if started_in_comment else re.fullmatch(r" {0,3}=+[ \t]*", raw_line)
        )
        if setext is not None and previous_line:
            return " ".join(previous_line.split())

        raw_atx = None if started_in_comment else re.match(r" {0,3}#[ \t]+", raw_line)
        atx = (
            re.fullmatch(r" {0,3}#[ \t]+(.+?)", visible_line)
            if raw_atx is not None
            else None
        )
        if atx is not None:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", atx.group(1))
            normalized = " ".join(title.split())
            if normalized:
                return normalized

        stripped = visible_line.strip()
        previous_line = stripped if stripped else ""
    return None


class IngestionService:
    def __init__(
        self,
        extractor: InputExtractor,
        store: RuntimeFileStore,
        *,
        policy: ChunkPolicy = DEFAULT_CHUNK_POLICY,
    ) -> None:
        self._extractor = extractor
        self._store = store
        self._policy = policy

    def preview(
        self, snapshot: GraphSnapshot, request: IngestionRequest
    ) -> IngestionPreview:
        if request.source_type_id not in {item.id for item in snapshot.source_types}:
            raise IngestionError("source_type_id does not resolve exactly")
        self._validate_optional_ids(snapshot, request)
        request = self._with_resolved_identity(request)
        identity = request.external_id or request.source_url
        extracted = self._extractor.extract(request)
        request = self._with_resolved_title(request, extracted.text)
        document_id = self._find_document(snapshot, request) or stable_id(
            "doc", request.kind, identity
        )
        prior = self._versions(snapshot, document_id)
        version = max((item.version for item in prior), default=0) + 1
        texts = (
            ()
            if request.kind == "image_collection"
            else split_text(extracted.text, self._policy)
        )
        if request.kind != "image_collection" and not texts:
            raise IngestionError("text input produced no nonblank chunks")
        digest = hashlib.sha256(extracted.content).hexdigest()
        return IngestionPreview(
            request=request,
            extracted=extracted,
            document_id=document_id,
            version=version,
            relative_directory=f"sources/{document_id}/v{version}",
            content_sha256=digest,
            chunk_texts=texts,
        )

    def commit(
        self,
        snapshot: GraphSnapshot,
        preview: IngestionPreview,
        *,
        created_at: datetime,
    ) -> CommitResult:
        request = preview.request
        existing_id = self._find_document(snapshot, request)
        if existing_id is not None and existing_id != preview.document_id:
            raise IngestionError("preview is stale")
        if (
            preview.version
            != max(
                (
                    item.version
                    for item in self._versions(snapshot, preview.document_id)
                ),
                default=0,
            )
            + 1
        ):
            raise IngestionError("preview is stale")
        version_id = stable_id("version", preview.document_id, str(preview.version))
        source_path = (
            f"{preview.relative_directory}/source{preview.extracted.extension}"
        )
        files: list[tuple[str, bytes]] = [(source_path, preview.extracted.content)]
        assets: list[graph.Asset] = []
        for position, item in enumerate(preview.extracted.assets):
            asset_id = stable_id(
                "asset",
                version_id,
                str(position),
                hashlib.sha256(item.content).hexdigest(),
            )
            path = f"{preview.relative_directory}/assets/{position:04d}-{item.name}"
            files.append((path, item.content))
            assets.append(
                graph.Asset(
                    id=asset_id,
                    asset_type="image",
                    mime_type=item.mime_type,
                    source_url="",
                    local_path=path,
                    external_id=item.external_id,
                    sha256=hashlib.sha256(item.content).hexdigest(),
                    position=position,
                    caption=item.caption,
                    llm_description="",
                    ocr_text=item.ocr_text,
                )
            )
        chunks = make_chunks(version_id, preview.chunk_texts)
        new_snapshot = self._build_snapshot(
            snapshot,
            preview,
            created_at,
            source_path,
            version_id,
            chunks,
            tuple(assets),
        )
        new_snapshot = _validated_snapshot(new_snapshot)
        written = self._store.write_files(tuple(files))
        return CommitResult(snapshot=new_snapshot, written_paths=written)

    @staticmethod
    def _with_resolved_title(
        request: IngestionRequest, extracted_text: str
    ) -> IngestionRequest:
        normalized = " ".join(request.title.split())
        if normalized:
            return request.model_copy(update={"title": normalized})
        if request.kind != "markdown":
            raise IngestionError("title is required for non-Markdown input")

        heading = _markdown_h1(extracted_text)
        if heading is not None:
            return request.model_copy(update={"title": heading})

        filename = PurePosixPath(request.input_path).stem
        normalized_filename = " ".join(filename.split())
        if not normalized_filename or normalized_filename == ".":
            raise IngestionError("Markdown title could not be generated")
        return request.model_copy(update={"title": normalized_filename})

    @staticmethod
    def _with_resolved_identity(request: IngestionRequest) -> IngestionRequest:
        if request.external_id or request.source_url:
            return request
        if request.kind == "image_collection":
            raise IngestionError(
                "external_id or source_url is required for an image collection"
            )
        if request.kind == "url" or not request.input_path:
            raise IngestionError("external_id, source_url, or local path is required")
        return request.model_copy(update={"external_id": f"local:{request.input_path}"})

    @staticmethod
    def _find_document(
        snapshot: GraphSnapshot, request: IngestionRequest
    ) -> str | None:
        matches = [
            item.id
            for item in snapshot.documents
            if (request.external_id and item.external_id == request.external_id)
            or (
                not request.external_id
                and request.source_url
                and item.source_url == request.source_url
            )
        ]
        if len(matches) > 1:
            raise IngestionError("source identity is ambiguous")
        return matches[0] if matches else None

    @staticmethod
    def _versions(
        snapshot: GraphSnapshot, document_id: str
    ) -> tuple[graph.DocumentVersion, ...]:
        ids = {
            item.target_id
            for item in snapshot.has_versions
            if item.source_id == document_id
        }
        return tuple(item for item in snapshot.document_versions if item.id in ids)

    @staticmethod
    def _validate_optional_ids(
        snapshot: GraphSnapshot, request: IngestionRequest
    ) -> None:
        for value, ids, label in (
            (request.status_id, {x.id for x in snapshot.statuses}, "status_id"),
            (request.process_id, {x.id for x in snapshot.processes}, "process_id"),
            (request.role_id, {x.id for x in snapshot.roles}, "role_id"),
        ):
            if value is not None and value not in ids:
                raise IngestionError(f"{label} does not resolve exactly")

    @staticmethod
    def _build_snapshot(
        snapshot: GraphSnapshot,
        preview: IngestionPreview,
        created_at: datetime,
        source_path: str,
        version_id: str,
        chunks: tuple[graph.Chunk, ...],
        assets: tuple[graph.Asset, ...],
    ) -> GraphSnapshot:
        request = preview.request
        old_versions = tuple(
            item.model_copy(update={"is_current": False})
            if item.is_current
            and item in IngestionService._versions(snapshot, preview.document_id)
            else item
            for item in snapshot.document_versions
        )
        document = graph.Document(
            id=preview.document_id,
            title=request.title,
            source_name=request.source_name,
            content_format=request.kind,
            source_url=request.source_url,
            external_id=request.external_id,
            is_active=True,
        )
        documents = tuple(
            item for item in snapshot.documents if item.id != preview.document_id
        ) + (document,)
        version = graph.DocumentVersion(
            id=version_id,
            version=preview.version,
            sha256=preview.content_sha256,
            local_path=source_path,
            created_at=created_at,
            is_current=True,
            error="",
        )
        prior = IngestionService._versions(snapshot, preview.document_id)
        supersedes = tuple(
            item for item in snapshot.supersedes if item.source_id != version_id
        )
        if prior:
            latest = max(prior, key=lambda item: (item.version, item.id))
            supersedes += (
                graph.Supersedes.model_validate({"from": version_id, "to": latest.id}),
            )
        return snapshot.model_copy(
            update={
                "documents": documents,
                "document_versions": old_versions + (version,),
                "chunks": snapshot.chunks + chunks,
                "assets": snapshot.assets + assets,
                "has_source_types": tuple(
                    item
                    for item in snapshot.has_source_types
                    if item.source_id != preview.document_id
                )
                + (
                    graph.HasSourceType.model_validate(
                        {"from": preview.document_id, "to": request.source_type_id}
                    ),
                ),
                "has_versions": snapshot.has_versions
                + (
                    graph.HasVersion.model_validate(
                        {"from": preview.document_id, "to": version_id}
                    ),
                ),
                "supersedes": supersedes,
                "has_chunks": snapshot.has_chunks
                + tuple(
                    graph.HasChunk.model_validate({"from": version_id, "to": item.id})
                    for item in chunks
                ),
                "has_assets": snapshot.has_assets
                + tuple(
                    graph.HasAsset.model_validate({"from": version_id, "to": item.id})
                    for item in assets
                ),
                "has_statuses": snapshot.has_statuses
                + (
                    graph.HasStatus.model_validate(
                        {"from": version_id, "to": request.status_id}
                    ),
                ),
                "describes": _replace_optional_relation(
                    snapshot.describes,
                    preview.document_id,
                    request.process_id,
                    graph.Describes,
                ),
                "for_roles": _replace_optional_relation(
                    snapshot.for_roles,
                    preview.document_id,
                    request.role_id,
                    graph.ForRole,
                ),
            }
        )


def _replace_optional_relation(
    relations: tuple[graph.Relation, ...],
    document_id: str,
    target_id: str | None,
    relation_type: type[graph.Relation],
) -> tuple[graph.Relation, ...]:
    if target_id is None:
        return relations
    retained = tuple(item for item in relations if item.source_id != document_id)
    return retained + (
        relation_type.model_validate({"from": document_id, "to": target_id}),
    )


def _validated_snapshot(snapshot: GraphSnapshot) -> GraphSnapshot:
    try:
        validated = GraphSnapshot.model_validate(snapshot.model_dump())
    except ValueError as error:
        raise IngestionError("committed graph would be invalid") from error
    if not validate_graph(validated).is_valid:
        raise IngestionError("committed graph would violate graph invariants")
    return validated
