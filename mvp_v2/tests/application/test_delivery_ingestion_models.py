from __future__ import annotations

import os
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mvp_v2.adapters.filesystem.runtime_store import (
    ConfinedRuntimeFileStore,
    FileStoreError,
)
from mvp_v2.adapters.ingestion import LocalInputExtractor
from mvp_v2.adapters.ollama import OllamaAdapter
from mvp_v2.application.document_editing import (
    DocumentMetadataUpdate,
    update_document_metadata,
)
from mvp_v2.application.graph_snapshot_operations.ingestion import (
    IngestionError,
    IngestionService,
    _markdown_h1,
)
from mvp_v2.application.ingestion_contracts import (
    EmbeddingRequest,
    ExtractedAsset,
    ExtractedInput,
    IngestionRequest,
    ModelAuthorization,
    ModelIdentity,
    SourceKind,
)
from mvp_v2.application.rag.preparation.chunking import ChunkPolicy, split_text
from mvp_v2.application.rag.preparation.model_operations import (
    ModelOperationError,
    build_embeddings,
    describe_asset,
)
from mvp_v2.domain import graph, invariants
from mvp_v2.domain.schema_registry import NODE_SPECS, PropertySpec, ScalarType
from mvp_v2.domain.snapshot import GraphSnapshot

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Extractor:
    def __init__(self, value: ExtractedInput) -> None:
        self.value = value

    def extract(self, request: IngestionRequest) -> ExtractedInput:
        del request
        return self.value


class Store:
    def __init__(self) -> None:
        self.files: tuple[tuple[str, bytes], ...] = ()

    def write_files(self, files: tuple[tuple[str, bytes], ...]) -> tuple[str, ...]:
        self.files = files
        return tuple(path for path, _ in files)


class FakeModel:
    calls = 0

    def describe(self, *, image: bytes, mime_type: str, model: str) -> str:
        del image, mime_type, model
        self.calls += 1
        return "A blue diagram"

    def embed(
        self, *, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, ...], ...]:
        del model
        self.calls += 1
        return tuple((float(index), 1.0) for index, _ in enumerate(texts))


def base_snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        source_types=(graph.SourceType(id="source-web", code="web"),),
        statuses=(graph.Status(id="status-ingested", name="ingested", description=""),),
    )


def request(kind: SourceKind = "markdown") -> IngestionRequest:
    return IngestionRequest(
        kind=kind,
        title="Guide",
        source_type_id="source-web",
        status_id="status-ingested",
        external_id="guide-1",
        input_path="guide.md",
    )


def test_chunking_and_version_history_with_filesystem(tmp_path: Path) -> None:
    text = (
        "First paragraph has enough meaningful text.\n\n"
        "Second paragraph is also meaningful."
    )
    chunks = split_text(
        text,
        ChunkPolicy(max_characters=55, overlap_characters=5, minimum_characters=10),
    )
    assert chunks and all(value.strip() for value in chunks)
    store = ConfinedRuntimeFileStore(tmp_path)
    service = IngestionService(
        Extractor(ExtractedInput(content=text.encode(), text=text, extension=".md")),
        store,
    )
    first = service.commit(
        base_snapshot(), service.preview(base_snapshot(), request()), created_at=NOW
    ).snapshot
    second_preview = service.preview(first, request())
    second = service.commit(first, second_preview, created_at=NOW).snapshot
    versions = sorted(second.document_versions, key=lambda item: item.version)
    assert [(item.version, item.is_current) for item in versions] == [
        (1, False),
        (2, True),
    ]
    assert [(item.source_id, item.target_id) for item in second.supersedes] == [
        (versions[1].id, versions[0].id)
    ]
    assert (tmp_path / versions[1].local_path).read_text() == text


def test_explicit_document_target_creates_new_version_safely() -> None:
    service = IngestionService(
        Extractor(ExtractedInput(content=b"content", text="content", extension=".md")),
        Store(),
    )
    initial_request = request().model_copy(update={"source_name": "Original source"})
    first_preview = service.preview(base_snapshot(), initial_request)
    first = service.commit(base_snapshot(), first_preview, created_at=NOW).snapshot
    document = first.documents[0]
    targeted = request().model_copy(
        update={
            "document_id": document.id,
            "external_id": document.external_id,
            "input_path": "replacement.md",
        }
    )

    preview = service.preview(first, targeted)

    assert preview.document_id == document.id
    assert preview.version == 2
    second = service.commit(first, preview, created_at=NOW).snapshot
    assert len(second.documents) == 1
    assert second.documents[0].source_name == "Original source"
    assert {item.version for item in second.document_versions} == {1, 2}
    with pytest.raises(IngestionError, match="external_id does not match"):
        service.preview(
            first,
            targeted.model_copy(update={"external_id": "another-document"}),
        )
    with pytest.raises(IngestionError, match="document_id does not resolve"):
        service.preview(
            first,
            targeted.model_copy(update={"document_id": "missing-document"}),
        )


def test_local_path_generates_identity_and_reuses_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = IngestionService(
        Extractor(ExtractedInput(content=b"content", text="content", extension=".md")),
        Store(),
    )
    local_request = request().model_copy(
        update={"external_id": "", "source_url": "", "input_path": "guide.md"}
    )

    first_preview = service.preview(base_snapshot(), local_request)
    first = service.commit(base_snapshot(), first_preview, created_at=NOW).snapshot
    document_spec = next(item for item in NODE_SPECS if item.table_name == "Document")
    configured_spec = replace(
        document_spec,
        properties=(
            *document_spec.properties,
            PropertySpec(
                name="review_note",
                scalar_type=ScalarType.STRING,
                required_on_read=False,
                default="",
                custom=True,
            ),
        ),
    )
    monkeypatch.setattr(
        invariants,
        "NODE_SPECS",
        tuple(
            configured_spec if item.table_name == "Document" else item
            for item in NODE_SPECS
        ),
    )
    enriched_document = first.documents[0].model_copy(
        update={
            "summary": "Keep this summary",
            "custom_properties": (
                graph.CustomProperty(name="review_note", value="keep"),
            ),
        }
    )
    enriched = first.model_copy(update={"documents": (enriched_document,)})
    second_preview = service.preview(enriched, local_request)
    second = service.commit(enriched, second_preview, created_at=NOW).snapshot

    assert first_preview.request.external_id == "local:guide.md"
    assert first_preview.document_id.startswith("doc-")
    assert first.documents[0].external_id == "local:guide.md"
    assert second_preview.document_id == first_preview.document_id
    assert second_preview.version == 2
    assert second.documents[0].summary == "Keep this summary"
    assert second.documents[0].custom_properties == (
        graph.CustomProperty(name="review_note", value="keep"),
    )


def test_markdown_title_uses_h1_then_filename() -> None:
    store = Store()
    heading_service = IngestionService(
        Extractor(
            ExtractedInput(
                content=b"Generated title\n===============\n\nBody",
                text=(
                    "    # Indented code\n"
                    "<!--\n# Commented heading\n-->\n"
                    "```md\n# Fenced heading\n```\n"
                    "Generated title\n===============\n\nBody"
                ),
                extension=".md",
            )
        ),
        store,
    )
    untitled = request().model_copy(
        update={"title": "", "input_path": "Fallback name.md"}
    )

    heading_preview = heading_service.preview(base_snapshot(), untitled)
    filename_service = IngestionService(
        Extractor(ExtractedInput(content=b"Body", text="Body", extension=".md")),
        store,
    )
    filename_preview = filename_service.preview(base_snapshot(), untitled)

    assert heading_preview.request.title == "Generated title"
    assert filename_preview.request.title == "Fallback name"


def test_markdown_title_respects_fence_marker_and_length() -> None:
    service = IngestionService(
        Extractor(
            ExtractedInput(
                content=b"content",
                text=(
                    "````md\n# Fenced\n```\n```not-a-close\n"
                    "# Still fenced\n````\n# Real title"
                ),
                extension=".md",
            )
        ),
        Store(),
    )

    preview = service.preview(
        base_snapshot(), request().model_copy(update={"title": ""})
    )

    assert preview.request.title == "Real title"


@pytest.mark.parametrize(
    ("markdown", "expected"),
    (
        ("````md\n# Hidden\n```\n# Still hidden\n````\n# Real", "Real"),
        ("```md\n# Hidden\n~~~\n# Still hidden\n```\n# Real", "Real"),
        ("```md\n# Hidden\n``` trailing\n# Still hidden\n```\n# Real", "Real"),
        ("```bad`info\n# Real", "Real"),
        ("<!-- done -->```md\n# Real", "Real"),
    ),
)
def test_markdown_title_fence_rules_are_independent(
    markdown: str, expected: str
) -> None:
    assert _markdown_h1(markdown) == expected


def test_typed_title_overrides_markdown_generated_title() -> None:
    service = IngestionService(
        Extractor(
            ExtractedInput(content=b"# Heading", text="# Heading", extension=".md")
        ),
        Store(),
    )

    preview = service.preview(
        base_snapshot(), request().model_copy(update={"title": " Manual title "})
    )

    assert preview.request.title == "Manual title"


def test_non_markdown_input_still_requires_title() -> None:
    service = IngestionService(
        Extractor(ExtractedInput(content=b"pdf", text="text", extension=".pdf")),
        Store(),
    )

    with pytest.raises(IngestionError, match="title"):
        service.preview(
            base_snapshot(), request("pdf").model_copy(update={"title": ""})
        )


def test_image_collection_requires_explicit_identity() -> None:
    service = IngestionService(
        Extractor(ExtractedInput(content=b"manifest", text="", extension=".manifest")),
        Store(),
    )
    missing_identity = request("image_collection").model_copy(
        update={"external_id": "", "source_url": "", "input_path": "."}
    )

    with pytest.raises(IngestionError, match="image collection"):
        service.preview(base_snapshot(), missing_identity)


def test_image_collection_has_assets_and_no_chunks() -> None:
    extracted = ExtractedInput(
        content=b"manifest",
        text="",
        extension=".manifest",
        assets=(ExtractedAsset(name="a.png", content=b"png", mime_type="image/png"),),
    )
    store = Store()
    service = IngestionService(Extractor(extracted), store)
    preview = service.preview(base_snapshot(), request("image_collection"))
    result = service.commit(base_snapshot(), preview, created_at=NOW)
    assert result.snapshot.assets and not result.snapshot.chunks


def test_default_deny_and_description_update() -> None:
    asset = graph.Asset(
        id="asset-1",
        asset_type="image",
        mime_type="image/png",
        source_url="",
        local_path="a.png",
        external_id="",
        sha256="0" * 64,
        position=0,
        description="caption",
        ocr_text="ocr",
    )
    snapshot = GraphSnapshot(
        source_types=(graph.SourceType(id="source-web", code="web"),),
        documents=(
            graph.Document(
                id="doc",
                title="Doc",
                source_name="",
                content_format="image_collection",
                source_url="",
                external_id="doc",
                is_active=True,
            ),
        ),
        document_versions=(
            graph.DocumentVersion(
                id="version",
                version=1,
                sha256="1" * 64,
                local_path="source.manifest",
                created_at=NOW,
                is_current=True,
                error="",
            ),
        ),
        assets=(asset,),
        statuses=(graph.Status(id="status-ingested", name="ingested", description=""),),
        has_source_types=(
            graph.HasSourceType.model_validate({"from": "doc", "to": "source-web"}),
        ),
        has_versions=(
            graph.HasVersion.model_validate({"from": "doc", "to": "version"}),
        ),
        has_assets=(
            graph.HasAsset.model_validate({"from": "version", "to": "asset-1"}),
        ),
        has_statuses=(
            graph.HasStatus.model_validate(
                {"from": "version", "to": "status-ingested"}
            ),
        ),
    )
    model = FakeModel()
    with pytest.raises(ModelOperationError):
        describe_asset(
            snapshot,
            "asset-1",
            b"x",
            ModelAuthorization(allow=False, operation="vision_description"),
            model="TODO-MODEL",
            vision=model,
        )
    assert model.calls == 0
    updated = describe_asset(
        snapshot,
        "asset-1",
        b"x",
        ModelAuthorization(allow=True, operation="vision_description"),
        model="TODO-MODEL",
        vision=model,
    )
    assert updated.assets[0].description == "A blue diagram"
    assert (
        updated.assets[0].description == "A blue diagram"
        and updated.assets[0].ocr_text == "ocr"
    )


def test_missing_then_rebuild_preserves_embedding_history() -> None:
    chunk = graph.Chunk(
        id="chunk-1",
        position=0,
        content="retrieval text",
        content_hash="5db5d91e669089d47b7cfc827d10ee8e36abd7f749d000344b409ed16007b2c0",
        summary="short retrieval summary",
    )
    snapshot = GraphSnapshot(
        source_types=(graph.SourceType(id="source-web", code="web"),),
        documents=(
            graph.Document(
                id="doc",
                title="Doc",
                source_name="",
                content_format="markdown",
                source_url="",
                external_id="doc",
                is_active=True,
                summary="document overview",
            ),
        ),
        document_versions=(
            graph.DocumentVersion(
                id="version",
                version=1,
                sha256="1" * 64,
                local_path="source.md",
                created_at=NOW,
                is_current=True,
                error="",
            ),
        ),
        chunks=(chunk,),
        statuses=(graph.Status(id="status-ingested", name="ingested", description=""),),
        has_source_types=(
            graph.HasSourceType.model_validate({"from": "doc", "to": "source-web"}),
        ),
        has_versions=(
            graph.HasVersion.model_validate({"from": "doc", "to": "version"}),
        ),
        has_chunks=(
            graph.HasChunk.model_validate({"from": "version", "to": "chunk-1"}),
        ),
        has_statuses=(
            graph.HasStatus.model_validate(
                {"from": "version", "to": "status-ingested"}
            ),
        ),
    )
    model = FakeModel()
    identity = ModelIdentity(
        provider="ollama",
        model="TODO-MODEL",
        model_version="operator-version",
        dimension=2,
    )
    auth = ModelAuthorization(allow=True, operation="embedding")
    missing = build_embeddings(
        snapshot,
        EmbeddingRequest(
            identity=identity,
            authorization=auth,
            mode="missing",
            generation_token="one",
            created_at=NOW,
        ),
        model,
    )
    edited = update_document_metadata(
        missing,
        (
            DocumentMetadataUpdate(
                document_id="doc",
                title="Doc",
                source_url="",
                external_id="doc",
                summary="updated document overview",
                is_active=True,
            ),
        ),
    )
    old_document_embedding_id = missing.has_summary_embeddings[0].target_id
    assert not next(
        item for item in edited.embeddings if item.id == old_document_embedding_id
    ).is_current
    repaired = build_embeddings(
        edited,
        EmbeddingRequest(
            identity=identity,
            authorization=auth,
            mode="missing",
            generation_token="summary-edit",
            created_at=NOW,
        ),
        model,
    )
    current_document_embeddings = {
        item.id
        for item in repaired.embeddings
        if item.is_current
        and item.id in {edge.target_id for edge in repaired.has_summary_embeddings}
    }
    assert len(current_document_embeddings) == 1

    unchanged = build_embeddings(
        missing,
        EmbeddingRequest(
            identity=identity,
            authorization=auth,
            mode="missing",
            generation_token="two",
            created_at=NOW,
        ),
        model,
    )
    rebuilt = build_embeddings(
        unchanged,
        EmbeddingRequest(
            identity=identity,
            authorization=auth,
            mode="all",
            generation_token="two",
            created_at=NOW,
        ),
        model,
    )
    assert len(unchanged.embeddings) == 2
    assert len(unchanged.has_embedding_chunks) == 1
    assert len(unchanged.has_summary_embeddings) == 1
    chunk_embedding_id = unchanged.has_embedding_chunks[0].target_id
    chunk_embedding = next(
        item for item in unchanged.embeddings if item.id == chunk_embedding_id
    )
    assert len(chunk_embedding.summary_vector) == 2
    document_embedding_id = unchanged.has_summary_embeddings[0].target_id
    document_embedding = next(
        item for item in unchanged.embeddings if item.id == document_embedding_id
    )
    assert document_embedding.summary_vector == ()
    assert {item.dimension for item in rebuilt.embeddings} == {2}
    assert sorted(item.is_current for item in rebuilt.embeddings) == [
        False,
        False,
        True,
        True,
    ]
    assert model.calls == 5


def test_ollama_parser_uses_mock_transport() -> None:
    class Transport:
        def post(self, path: str, payload: bytes) -> bytes:
            assert path == "/api/embed" and b"TODO-MODEL" in payload
            return b'{"embeddings":[[1,2]]}'

    assert OllamaAdapter(Transport()).embed(texts=("x",), model="TODO-MODEL") == (
        (1.0, 2.0),
    )


def test_boundary_contracts_reject_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ModelIdentity(provider="ollama", model=" ", model_version="v1", dimension=0)
    with pytest.raises(ValidationError):
        EmbeddingRequest(
            identity=ModelIdentity(
                provider="ollama", model="m", model_version="v", dimension=2
            ),
            authorization=ModelAuthorization(allow=True, operation="embedding"),
            mode="missing",
            generation_token="token",
            created_at=datetime(2026, 1, 1),
        )


def test_explicit_process_role_relations_and_same_token_rebuild_denied() -> None:
    snapshot = base_snapshot().model_copy(
        update={
            "processes": (
                graph.Process(id="process-1", name="P", status="active", owner=""),
            ),
            "roles": (graph.Role(id="role-1", name="R"),),
        }
    )
    req = request().model_copy(update={"process_id": "process-1", "role_id": "role-1"})
    service = IngestionService(
        Extractor(ExtractedInput(content=b"content", text="content", extension=".md")),
        Store(),
    )
    committed = service.commit(
        snapshot, service.preview(snapshot, req), created_at=NOW
    ).snapshot
    assert committed.describes[0].target_id == "process-1"
    assert committed.for_roles[0].target_id == "role-1"
    model = FakeModel()
    identity = ModelIdentity(
        provider="ollama", model="m", model_version="v", dimension=2
    )
    auth = ModelAuthorization(allow=True, operation="embedding")
    first = build_embeddings(
        committed,
        EmbeddingRequest(
            identity=identity,
            authorization=auth,
            mode="all",
            generation_token="same",
            created_at=NOW,
        ),
        model,
    )
    calls = model.calls
    with pytest.raises(ModelOperationError):
        build_embeddings(
            first,
            EmbeddingRequest(
                identity=identity,
                authorization=auth,
                mode="all",
                generation_token="same",
                created_at=NOW,
            ),
            model,
        )
    assert model.calls == calls


def test_image_manifest_excludes_raw_bytes(tmp_path: Path) -> None:
    from io import BytesIO

    from PIL import Image

    collection = tmp_path / "images"
    collection.mkdir()
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    raw = output.getvalue()
    (collection / "a.png").write_bytes(raw)
    extracted = LocalInputExtractor(tmp_path).extract(
        request("image_collection").model_copy(update={"input_path": "images"})
    )
    assert raw not in extracted.content
    assert b"a.png\timage/png\t" in extracted.content


def test_filesystem_partial_finalize_is_cleaned_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfinedRuntimeFileStore(tmp_path)
    original_link = os.link
    calls = 0

    def fail_second(
        source: Path, target: Path, *, follow_symlinks: bool = True
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated")
        original_link(source, target, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "link", fail_second)
    with pytest.raises(FileStoreError):
        store.write_files((("a/one", b"1"), ("a/two", b"2")))
    assert not (tmp_path / "a/one").exists()
    assert not (tmp_path / "a/two").exists()
    monkeypatch.setattr(os, "link", original_link)
    assert store.write_files((("a/one", b"1"), ("a/two", b"2"))) == ("a/one", "a/two")


def test_filesystem_reuses_only_identical_existing_files(tmp_path: Path) -> None:
    store = ConfinedRuntimeFileStore(tmp_path)
    files = (("source/document/v1/source.md", b"same content"),)

    assert store.write_files(files) == ("source/document/v1/source.md",)
    assert store.write_files(files) == ("source/document/v1/source.md",)

    with pytest.raises(FileStoreError, match="refusing to replace"):
        store.write_files((("source/document/v1/source.md", b"different"),))
    assert (tmp_path / "source/document/v1/source.md").read_bytes() == b"same content"


def test_filesystem_lock_close_failure_uses_store_error_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = os.open
    original_close = os.close
    lock_descriptors: list[int] = []

    def tracked_open(path: Path, flags: int, mode: int = 0o777) -> int:
        descriptor = original_open(path, flags, mode)
        if Path(path).name == ".ingestion.lock":
            lock_descriptors.append(descriptor)
        return descriptor

    def fail_lock_close(descriptor: int) -> None:
        if descriptor in lock_descriptors:
            raise OSError("simulated lock close failure")
        original_close(descriptor)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", fail_lock_close)
    try:
        with pytest.raises(FileStoreError, match="lock cleanup failed"):
            ConfinedRuntimeFileStore(tmp_path).write_files((("a", b"1"),))
    finally:
        for descriptor in lock_descriptors:
            original_close(descriptor)

    assert (tmp_path / "a").read_bytes() == b"1"


def test_filesystem_lock_close_failure_does_not_mask_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = os.open
    original_close = os.close
    lock_descriptors: list[int] = []

    def tracked_open(path: Path, flags: int, mode: int = 0o777) -> int:
        descriptor = original_open(path, flags, mode)
        if Path(path).name == ".ingestion.lock":
            lock_descriptors.append(descriptor)
        return descriptor

    def fail_lock_close(descriptor: int) -> None:
        if descriptor in lock_descriptors:
            raise OSError("simulated lock close failure")
        original_close(descriptor)

    def fail_link(source: Path, target: Path, *, follow_symlinks: bool = True) -> None:
        del source, target, follow_symlinks
        raise OSError("simulated write failure")

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", fail_lock_close)
    monkeypatch.setattr(os, "link", fail_link)
    try:
        with pytest.raises(FileStoreError, match="runtime file write failed"):
            ConfinedRuntimeFileStore(tmp_path).write_files((("a", b"1"),))
    finally:
        for descriptor in lock_descriptors:
            original_close(descriptor)


def test_filesystem_concurrent_reuse_survives_other_writer_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_linked = threading.Event()
    second_writer_started = threading.Event()
    allow_first_writer_to_fail = threading.Event()
    original_link = os.link
    first_writer_link_count = 0
    errors: list[BaseException] = []
    second_result: list[tuple[str, ...]] = []
    files = (("a/one", b"1"), ("a/two", b"2"))

    def controlled_link(
        source: Path, target: Path, *, follow_symlinks: bool = True
    ) -> None:
        nonlocal first_writer_link_count
        if threading.current_thread().name == "first-writer":
            first_writer_link_count += 1
            if first_writer_link_count == 1:
                original_link(source, target, follow_symlinks=follow_symlinks)
                first_linked.set()
                assert allow_first_writer_to_fail.wait(timeout=2)
                return
            raise OSError("simulated second-file failure")
        original_link(source, target, follow_symlinks=follow_symlinks)

    def first_writer() -> None:
        try:
            ConfinedRuntimeFileStore(tmp_path).write_files(files)
        except BaseException as error:  # noqa: BLE001 - captured across test thread
            errors.append(error)

    def second_writer() -> None:
        second_writer_started.set()
        second_result.append(ConfinedRuntimeFileStore(tmp_path).write_files(files))

    monkeypatch.setattr(os, "link", controlled_link)
    first = threading.Thread(target=first_writer, name="first-writer")
    second = threading.Thread(target=second_writer, name="second-writer")
    first.start()
    assert first_linked.wait(timeout=2)
    second.start()
    assert second_writer_started.wait(timeout=2)
    allow_first_writer_to_fail.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], FileStoreError)
    assert second_result == [("a/one", "a/two")]
    assert (tmp_path / "a/one").read_bytes() == b"1"
    assert (tmp_path / "a/two").read_bytes() == b"2"


def test_filesystem_race_never_replaces_different_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfinedRuntimeFileStore(tmp_path)
    target = tmp_path / "source/document/v1/source.md"
    original_link = os.link

    def create_competing_target(
        source: Path, destination: Path, *, follow_symlinks: bool = True
    ) -> None:
        del source, follow_symlinks
        Path(destination).write_bytes(b"competing content")
        raise FileExistsError

    monkeypatch.setattr(os, "link", create_competing_target)
    with pytest.raises(FileStoreError, match="refusing to replace"):
        store.write_files((("source/document/v1/source.md", b"our content"),))

    monkeypatch.setattr(os, "link", original_link)
    assert target.read_bytes() == b"competing content"


def test_filesystem_race_accepts_identical_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfinedRuntimeFileStore(tmp_path)

    def create_identical_target(
        source: Path, destination: Path, *, follow_symlinks: bool = True
    ) -> None:
        del source, follow_symlinks
        Path(destination).write_bytes(b"same content")
        raise FileExistsError

    monkeypatch.setattr(os, "link", create_identical_target)
    paths = store.write_files((("source/document/v1/source.md", b"same content"),))

    assert paths == ("source/document/v1/source.md",)


def test_invalid_graph_is_rejected_before_file_store_call() -> None:
    orphan = graph.Chunk(
        id="orphan",
        position=0,
        content="orphan content",
        content_hash="17890bb8ba65da9d9dd434d2019133046d212cba2bbcdc0fcab3e9cd28f2a106",
    )
    snapshot = base_snapshot().model_copy(update={"chunks": (orphan,)})
    store = Store()
    service = IngestionService(
        Extractor(ExtractedInput(content=b"content", text="content", extension=".md")),
        store,
    )
    preview = service.preview(snapshot, request())
    with pytest.raises(IngestionError, match="graph invariants"):
        service.commit(snapshot, preview, created_at=NOW)
    assert store.files == ()
