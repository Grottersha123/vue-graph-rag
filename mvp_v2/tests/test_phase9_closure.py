from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from mvp_v2.adapters.filesystem.media import FilesystemMediaResolver
from mvp_v2.application.chat import NOT_ENOUGH_EVIDENCE, ChatError, ChatService
from mvp_v2.application.dto.chat import ChatRequest
from mvp_v2.application.dto.importing import canonical_json_bytes
from mvp_v2.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    Evidence,
    SearchRequest,
    SubjectKind,
    SubjectRef,
)
from mvp_v2.application.ingestion_contracts import IngestionRequest, ModelAuthorization
from mvp_v2.application.relations import RelationError, RelationKind, RelationService
from mvp_v2.ports.media import MediaVerificationError
from mvp_v2.presentation.controller import (
    commit_ingestion,
    configure_ladybug_home,
    database_is_stale,
    parse_runtime_config,
    preview_ingestion,
    scan_database_manifests,
    stage_uploads,
)
from tests.application.test_contracts_and_manifests import make_database_manifest
from tests.factories import build_valid_snapshot


@dataclass
class Upload:
    name: str
    content: bytes = b"ok"

    def getvalue(self) -> bytes:
        return self.content


class Search:
    def __init__(self, evidence: tuple[Evidence, ...]) -> None:
        self.evidence = evidence

    def search(self, request: SearchRequest) -> tuple[Evidence, ...]:
        return self.evidence


class Model:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, *, prompt: str, model: str) -> str:
        self.calls.append(prompt)
        return "Bound answer [1]"


def evidence() -> Evidence:
    return Evidence(
        subject=SubjectRef(kind=SubjectKind.CHUNK, id="chunk"),
        embedding_id="embedding-chunk",
        score=0.9,
        text="fact",
        document_id="document",
        document_title="Title",
        source_url="",
        version_id="version",
        version=1,
        local_path="documents/a.md",
        source_type="local_file",
        statuses=("ready",),
        roles=("student",),
        processes=("onboarding",),
        linked_document_ids=(),
    )


def request(*, generation: bool = True) -> ChatRequest:
    search = SearchRequest(
        question="Question?",
        space=EmbeddingSpaceIdentity(
            provider="ollama", model="embed", model_version="v1", dimension=2
        ),
        purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
        limit=8,
        authorization=ModelAuthorization(allow=True, operation="embedding"),
    )
    return ChatRequest(
        question="Question?",
        search=search,
        model="chat",
        model_version="v1",
        authorization=ModelAuthorization(allow=generation, operation="generation"),
    )


def test_chat_denied_with_evidence_makes_zero_generation_calls() -> None:
    model = Model()
    with pytest.raises(ChatError):
        ChatService(Search((evidence(),)), model).answer(request(generation=False))
    assert model.calls == []


def test_chat_without_evidence_has_fixed_answer_and_zero_generation_calls() -> None:
    model = Model()
    answer = ChatService(Search(()), model).answer(request())
    assert (answer.answer, answer.generated, model.calls) == (
        NOT_ENOUGH_EVIDENCE,
        False,
        [],
    )


def test_chat_binds_response_and_numbered_citation() -> None:
    model = Model()
    answer = ChatService(Search((evidence(),)), model).answer(request())
    assert answer.answer == "Bound answer [1]"
    assert answer.citations[0].number == 1
    assert answer.citations[0].evidence_id == answer.evidence[0].embedding_id
    assert len(model.calls[0]) < 16_000


def test_relation_add_delete_and_duplicate_invariants() -> None:
    snapshot = build_valid_snapshot().model_copy(update={"describes": ()})
    service = RelationService()
    added = service.add(snapshot, RelationKind.DESCRIBES, "document", "process")
    with pytest.raises(RelationError):
        service.add(added, RelationKind.DESCRIBES, "document", "process")
    assert (
        service.delete(added, RelationKind.DESCRIBES, "document", "process").describes
        == ()
    )


@pytest.mark.parametrize("name", ("../escape", "a/b", "a\\b", ".hidden"))
def test_upload_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        stage_uploads(tmp_path, (Upload(name),))


def test_upload_does_not_overwrite_duplicate_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        stage_uploads(tmp_path, (Upload("a.txt"), Upload("a.txt")))


def test_media_reader_requires_sha_and_returns_exact_bytes(tmp_path: Path) -> None:
    media = tmp_path / "images" / "a.png"
    media.parent.mkdir()
    media.write_bytes(b"image")
    resolver = FilesystemMediaResolver(tmp_path)
    digest = hashlib.sha256(b"image").hexdigest()
    assert resolver.read_verified("images/a.png", digest) == b"image"
    with pytest.raises(MediaVerificationError):
        resolver.read_verified("images/a.png", "0" * 64)


def test_invalid_runtime_config_is_not_replaced_by_defaults() -> None:
    with pytest.raises(ValueError, match="all operations are blocked"):
        parse_runtime_config(
            {
                "working_root": "/tmp/out",
                "dumps_root": "/tmp/out",
                "databases_root": "/tmp/out",
                "ladybug_home": "/tmp/out",
            }
        )


def test_streamlit_absent_runtime_starts_with_eight_live_tabs() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(Path(__file__).parents[1] / "streamlit_app.py").run(
        timeout=10
    )
    assert not app.exception
    assert [tab.label for tab in app.tabs] == [
        "Upload",
        "Documents",
        "Relations",
        "Chunks & Embeddings",
        "LadybugDB",
        "Search",
        "Chatbot",
        "Errors/Import report",
    ]
    labels = {button.label for button in app.button}
    assert {
        "Preview",
        "Build missing embeddings",
        "Save as new DB",
        "Search evidence",
        "Clear chat history",
    } <= labels


def test_database_scan_and_stale_decision_never_opens_native_db(tmp_path: Path) -> None:
    artifact = tmp_path / "safe.lbdb"
    artifact.mkdir()
    (artifact / "database.lbug").write_bytes(b"not a native database")
    manifest = make_database_manifest()
    (artifact / "_mvp_v2_manifest.json").write_bytes(
        canonical_json_bytes(manifest.model_dump(mode="json"))
    )
    assert scan_database_manifests(tmp_path) == ((artifact, manifest),)
    assert database_is_stale(manifest, None)
    (artifact / "_mvp_v2_manifest.json").write_text("{}")
    assert scan_database_manifests(tmp_path) == ()


def test_controller_preview_is_pure_then_commit_changes_snapshot(
    tmp_path: Path,
) -> None:
    snapshot = build_valid_snapshot().model_copy(
        update={
            "documents": (),
            "document_versions": (),
            "chunks": (),
            "assets": (),
            "embeddings": (),
            "has_source_types": (),
            "has_versions": (),
            "has_statuses": (),
            "has_chunks": (),
            "has_assets": (),
            "contains_assets": (),
            "has_embedding_chunks": (),
            "has_embedding_assets": (),
            "describes": (),
            "for_roles": (),
        }
    )
    (tmp_path / "guide.md").write_text("A useful local guide.")
    before = snapshot.model_dump_json()
    preview = preview_ingestion(
        snapshot,
        IngestionRequest(
            kind="markdown",
            title="Guide",
            source_type_id="source",
            status_id="status",
            external_id="guide",
            input_path="guide.md",
        ),
        working_root=tmp_path,
        input_root=tmp_path,
    )
    assert snapshot.model_dump_json() == before
    committed = commit_ingestion(snapshot, preview, working_root=tmp_path)
    assert committed != snapshot and committed.documents


def test_image_collection_rejects_fake_image_bytes(tmp_path: Path) -> None:
    from mvp_v2.adapters.ingestion import ExtractionError, LocalInputExtractor

    collection = tmp_path / "images"
    collection.mkdir()
    (collection / "fake.png").write_bytes(b"not an image")
    request_value = IngestionRequest(
        kind="image_collection",
        title="Images",
        source_type_id="source",
        status_id="status",
        external_id="images",
        input_path="images",
    )
    with pytest.raises(ExtractionError, match="image content"):
        LocalInputExtractor(tmp_path).extract(request_value)


def test_image_collection_uses_decoded_canonical_mime(tmp_path: Path) -> None:
    from io import BytesIO

    from PIL import Image

    from mvp_v2.adapters.ingestion import LocalInputExtractor

    collection = tmp_path / "images"
    collection.mkdir()
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    (collection / "misleading.jpg").write_bytes(output.getvalue())
    request_value = IngestionRequest(
        kind="image_collection",
        title="Images",
        source_type_id="source",
        status_id="status",
        external_id="images",
        input_path="images",
    )
    extracted = LocalInputExtractor(tmp_path).extract(request_value)
    assert extracted.assets[0].mime_type == "image/png"


def test_configure_ladybug_home_sets_exact_validated_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    paths = {
        name: runtime / name
        for name in ("working", "dumps", "databases", "ladybug-home")
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    from mvp_v2.bootstrap.ui import UiRuntimeConfig

    config = UiRuntimeConfig.model_construct(
        working_root=paths["working"],
        dumps_root=paths["dumps"],
        databases_root=paths["databases"],
        ladybug_home=paths["ladybug-home"],
    )
    monkeypatch.delenv("HOME", raising=False)
    configure_ladybug_home(config)
    assert os.environ["HOME"] == str(paths["ladybug-home"].resolve())


def test_application_logging_is_idempotent_and_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import logging

    from mvp_v2.presentation import logging as app_logging

    path = tmp_path / "runtime" / "logs" / "app.log"
    monkeypatch.setattr(app_logging, "application_log_path", lambda: path)
    path.parent.mkdir(parents=True)
    path.write_text("2026-09-15T13:00:00+0000 ERROR mvp_v2 database build failed\n")
    assert "database build failed" in app_logging.read_log_tail()
    assert logging.getLogger("mvp_v2").name == "mvp_v2"
