from __future__ import annotations

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
from mvp_v2.application.chunking import ChunkPolicy, split_text
from mvp_v2.application.ingestion import IngestionError, IngestionService
from mvp_v2.application.ingestion_contracts import (
    EmbeddingRequest,
    ExtractedAsset,
    ExtractedInput,
    IngestionRequest,
    ModelAuthorization,
    ModelIdentity,
    SourceKind,
)
from mvp_v2.application.model_operations import (
    ModelOperationError,
    build_embeddings,
    describe_asset,
)
from mvp_v2.domain import graph
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
        caption="caption",
        llm_description="",
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
    assert updated.assets[0].llm_description == "A blue diagram"
    assert (
        updated.assets[0].caption == "caption" and updated.assets[0].ocr_text == "ocr"
    )


def test_missing_then_rebuild_preserves_embedding_history() -> None:
    chunk = graph.Chunk(
        id="chunk-1",
        position=0,
        content="retrieval text",
        content_hash="5db5d91e669089d47b7cfc827d10ee8e36abd7f749d000344b409ed16007b2c0",
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
    assert len(unchanged.embeddings) == 1
    assert {(item.purpose, item.dimension) for item in rebuilt.embeddings} == {
        ("chunk_retrieval", 2)
    }
    assert sorted(item.is_current for item in rebuilt.embeddings) == [False, True]


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
    original_replace = Path.replace
    calls = 0

    def fail_second(path: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(FileStoreError):
        store.write_files((("a/one", b"1"), ("a/two", b"2")))
    assert not (tmp_path / "a/one").exists()
    assert not (tmp_path / "a/two").exists()
    monkeypatch.setattr(Path, "replace", original_replace)
    assert store.write_files((("a/one", b"1"), ("a/two", b"2"))) == ("a/one", "a/two")


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
