from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from mvp_v2.adapters.filesystem.media import FilesystemMediaResolver
from mvp_v2.application.chat import NOT_ENOUGH_EVIDENCE, ChatError, ChatService
from mvp_v2.application.dto.chat import ChatRequest
from mvp_v2.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    Evidence,
    SearchRequest,
    SubjectKind,
    SubjectRef,
)
from mvp_v2.application.ingestion_contracts import IngestionRequest, ModelAuthorization
from mvp_v2.application.json_encoding import canonical_json_bytes
from mvp_v2.application.relations import RelationError, RelationKind, RelationService
from mvp_v2.domain import graph
from mvp_v2.domain.schema_registry import (
    NODE_SPECS,
    SCHEMA_PROFILE_SHA256,
    PropertySpec,
    ScalarType,
)
from mvp_v2.ports.media import MediaVerificationError
from mvp_v2.presentation.app import (
    _asset_label,
    _clear_upload_state,
    _current_upload_preview,
    _custom_property_names,
    _deletion_block_reason,
    _invalidate_upload_preview,
    _node_record,
    _preferred_option_index,
    _run_visible_operation,
    _upload_input_fingerprint,
    _working_root_identity,
)
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


class SequenceModel:
    def __init__(self, responses: tuple[str, ...]) -> None:
        self.responses = iter(responses)
        self.calls: list[str] = []

    def generate(self, *, prompt: str, model: str) -> str:
        self.calls.append(prompt)
        return next(self.responses)


def evidence() -> Evidence:
    return Evidence(
        subject=SubjectRef(kind=SubjectKind.CHUNK, id="chunk"),
        embedding_id="embedding-chunk",
        score=0.9,
        text="fact",
        matched_chunk_position=0,
        document_chunk_count=1,
        context_scope="full_document",
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
    assert "Respond in Russian." in model.calls[0]
    assert len(model.calls[0]) < 16_000


def test_chat_retries_once_for_out_of_range_citation() -> None:
    model = SequenceModel(("Wrong citation [15]", "Correct answer:\n- first item [1]"))

    answer = ChatService(Search((evidence(),)), model).answer(request())

    assert answer.answer == "Correct answer:\n- first item [1]"
    assert len(model.calls) == 2
    assert "The only allowed citation tokens are: [1]." in model.calls[1]


def test_chat_parses_combined_and_nonsense_citation_tokens() -> None:
    model = SequenceModel(("Ответ [1, 2] и [заметка]",))

    answer = ChatService(Search((evidence(), evidence())), model).answer(request())

    assert [citation.number for citation in answer.citations] == [1, 2]


def test_chat_rejects_persistently_invalid_citation_after_one_retry() -> None:
    model = SequenceModel(("Wrong [15]", "Still wrong [15]"))

    with pytest.raises(ChatError, match="cites no valid evidence number"):
        ChatService(Search((evidence(),)), model).answer(request())

    assert len(model.calls) == 2


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


def test_visible_operation_shows_timed_spinner_and_runs_logged_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mvp_v2.presentation import app as presentation

    observed: list[object] = []

    @contextmanager
    def spinner(message: str, *, show_time: bool) -> Iterator[None]:
        observed.append((message, show_time))
        yield

    def run_logged(operation: str, action: Callable[[], int]) -> int:
        observed.append(operation)
        return action()

    monkeypatch.setattr(presentation.st, "spinner", spinner)
    monkeypatch.setattr(presentation, "run_logged_operation", run_logged)

    result = _run_visible_operation("database_build", "Building…", lambda: 42)

    assert result == 42
    assert observed == [("Building…", True), "database_build"]


def test_asset_label_uses_description_then_local_path() -> None:
    asset = build_valid_snapshot().assets[0]
    assert _asset_label(asset) == "desc"
    assert _asset_label(asset.model_copy(update={"description": ""})) == "images/a.png"


def test_working_root_identity_changes_with_runtime_root(tmp_path: Path) -> None:
    from mvp_v2.bootstrap.ui import UiRuntimeConfig

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = UiRuntimeConfig.model_construct(working_root=first_root)
    second = UiRuntimeConfig.model_construct(working_root=second_root)

    assert _working_root_identity(first) != _working_root_identity(second)


def test_deletion_block_reason_does_not_require_active_database() -> None:
    assert (
        _deletion_block_reason(
            blocked=False, selected_document_id="document", snapshot_dirty=False
        )
        is None
    )
    assert (
        _deletion_block_reason(
            blocked=False, selected_document_id=None, snapshot_dirty=False
        )
        == "Select one document to enable deletion preview."
    )
    assert (
        _deletion_block_reason(
            blocked=False, selected_document_id="document", snapshot_dirty=True
        )
        == "Publish or reload the snapshot before deleting a document."
    )


def test_preferred_option_index_matches_id_or_label_case_insensitively() -> None:
    assert (
        _preferred_option_index(
            {"other": "Other", "process-id": "OnBoarding"}, "onboarding"
        )
        == 2
    )
    assert _preferred_option_index({"Student": "Learner"}, "student") == 1
    assert _preferred_option_index({"other": "Other"}, "student") == 0


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
        "Errors",
    ]
    labels = {button.label for button in app.button}
    assert {
        "Preview",
        "Build missing embeddings",
        "Save as new DB",
        "Search evidence",
        "Clear chat history",
        "Commit changes",
        "Initialize workspace",
        "Publish changes as new dump",
    } <= labels
    assert "Confirm publish current snapshot as next immutable dump" not in {
        checkbox.label for checkbox in app.checkbox
    }
    assert "Publish current snapshot" not in labels
    assert "Preview legacy import" not in labels
    assert "Approve and publish legacy import" not in labels
    assert any(
        item.value == "No canonical dump is loaded." for item in app.sidebar.info
    )
    assert any(
        "Workspace needs current reference data" in item.value for item in app.warning
    )
    assert any(item.value == "Active DB: none" for item in app.sidebar.caption)


def _upload_fingerprint(
    *, title: str, content: bytes, snapshot_revision: int = 1
) -> str:
    return _upload_input_fingerprint(
        kind="markdown",
        title=title,
        source_type_id="source",
        external_id="guide",
        status_id="status",
        source_url="",
        process_id="process",
        role_id="role",
        document_id="document",
        snapshot_revision=snapshot_revision,
        uploads=(Upload(name="guide.md", content=content),),
    )


def test_upload_preview_binding_rejects_changed_inputs() -> None:
    original = _upload_fingerprint(title="Guide", content=b"version one")
    changed_title = _upload_fingerprint(title="New title", content=b"version one")
    changed_file = _upload_fingerprint(title="Guide", content=b"version two")
    changed_snapshot = _upload_fingerprint(
        title="Guide", content=b"version one", snapshot_revision=2
    )
    state: dict[str, object] = {
        "version_document_preview": "preview",
        "version_document_preview_fingerprint": original,
    }

    assert (
        _current_upload_preview(
            state,
            preview_key="version_document_preview",
            fingerprint_key="version_document_preview_fingerprint",
            fingerprint=original,
        )
        == "preview"
    )
    for fingerprint in (changed_title, changed_file, changed_snapshot):
        assert (
            _current_upload_preview(
                state,
                preview_key="version_document_preview",
                fingerprint_key="version_document_preview_fingerprint",
                fingerprint=fingerprint,
            )
            is None
        )


def test_failed_repreview_cannot_leave_previous_preview_committable() -> None:
    state: dict[str, object] = {
        "preview": "successful-old-preview",
        "fingerprint": "f" * 64,
    }

    _invalidate_upload_preview(
        state,
        preview_key="preview",
        fingerprint_key="fingerprint",
    )

    assert state == {"preview": None, "fingerprint": None}


def test_reopening_version_dialog_clears_only_its_widget_state() -> None:
    state: dict[str, object] = {
        "version_document_external_id": "stale-id",
        "version_document_files": (b"stale",),
        "version_document_preview": "stale-preview",
        "version_document_preview_fingerprint": "f" * 64,
        "upload_title": "unrelated",
    }

    _clear_upload_state(state, "version_document")

    assert state == {"upload_title": "unrelated"}


def test_node_records_flatten_only_configured_custom_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = build_valid_snapshot()
    document_spec = next(item for item in NODE_SPECS if item.table_name == "Document")
    configured = replace(
        document_spec,
        properties=(
            *document_spec.properties,
            PropertySpec(
                name="review_note",
                scalar_type=ScalarType.STRING,
                required_on_read=False,
                default="not reviewed",
                custom=True,
            ),
        ),
    )
    specs = tuple(
        configured if item.table_name == "Document" else item for item in NODE_SPECS
    )
    monkeypatch.setattr("mvp_v2.presentation.app.NODE_SPECS", specs)
    document = snapshot.documents[0].model_copy(
        update={
            "custom_properties": (
                graph.CustomProperty(name="review_note", value="approved"),
            )
        }
    )

    document_record = _node_record("Document", document)
    chunk_record = _node_record("Chunk", snapshot.chunks[0])

    assert "custom_properties" not in document_record
    assert "custom_properties" not in chunk_record
    assert document_record["review_note"] == "approved"
    assert "review_note" not in chunk_record
    assert _custom_property_names("Document") == ("review_note",)
    assert _custom_property_names("Chunk") == ()


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
    foreign = make_database_manifest(schema_profile_sha256="f" * 64)
    assert foreign.schema_profile_sha256 != SCHEMA_PROFILE_SHA256
    (artifact / "_mvp_v2_manifest.json").write_bytes(
        canonical_json_bytes(foreign.canonical_payload())
    )
    assert scan_database_manifests(tmp_path) == ()
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
    from loguru import logger

    from mvp_v2.presentation import logging as app_logging

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(app_logging, "_runtime_root", lambda: runtime)
    monkeypatch.setattr(app_logging, "_SINK_ID", None)
    monkeypatch.setattr(app_logging, "_SINK_PATH", None)
    monkeypatch.delattr(logger, app_logging._LOGGER_STATE_ATTRIBUTE, raising=False)
    app_logging.configure_application_logging()
    first_sink = app_logging._SINK_ID
    app_logging.configure_application_logging()
    second_sink = app_logging._SINK_ID
    assert second_sink is not None
    assert second_sink == first_sink
    logger.remove(second_sink)
    app_logging.configure_application_logging()
    recovered_sink = app_logging._SINK_ID
    assert recovered_sink is not None
    assert recovered_sink != second_sink

    app_logging.record_user_action("Preview")
    assert app_logging.run_logged_operation("dump_publication", lambda: 42) == 42
    with pytest.raises(ValueError, match="broken"):
        app_logging.run_logged_operation(
            "database_build", lambda: (_ for _ in ()).throw(ValueError("broken"))
        )

    content = app_logging.read_log_tail()
    assert "ui_action clicked action=Preview" in content
    assert "dump_publication succeeded" in content
    assert "database_build failed" in content
    assert "Traceback" in content
    if recovered_sink is not None:
        logger.remove(recovered_sink)
    monkeypatch.delattr(logger, app_logging._LOGGER_STATE_ATTRIBUTE, raising=False)
    monkeypatch.setattr(app_logging, "_SINK_ID", None)
    monkeypatch.setattr(app_logging, "_SINK_PATH", None)


def test_application_logging_replaces_stale_process_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loguru import logger

    from mvp_v2.presentation import logging as app_logging

    monkeypatch.setattr(app_logging, "_runtime_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(app_logging, "_SINK_ID", None)
    monkeypatch.setattr(app_logging, "_SINK_PATH", None)
    monkeypatch.setattr(
        logger, app_logging._LOGGER_STATE_ATTRIBUTE, (2_147_483_647, "/stale/path")
    )

    app_logging.configure_application_logging()

    sink_id = app_logging._SINK_ID
    assert sink_id is not None
    app_logging.record_user_action("Preview")
    assert "clicked action=Preview" in app_logging.read_log_tail()
    logger.remove(sink_id)
    monkeypatch.delattr(logger, app_logging._LOGGER_STATE_ATTRIBUTE, raising=False)


def test_application_logging_concurrent_configuration_uses_one_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from loguru import logger

    from mvp_v2.presentation import logging as app_logging

    monkeypatch.setattr(app_logging, "_runtime_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(app_logging, "_SINK_ID", None)
    monkeypatch.setattr(app_logging, "_SINK_PATH", None)
    monkeypatch.delattr(logger, app_logging._LOGGER_STATE_ATTRIBUTE, raising=False)

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(
            executor.map(
                lambda _: app_logging.configure_application_logging(), range(32)
            )
        )
    sink_id = app_logging._SINK_ID
    assert sink_id is not None
    app_logging.record_user_action("Concurrent check")
    assert app_logging.read_log_tail().count("clicked action=Concurrent check") == 1

    logger.remove(sink_id)
    monkeypatch.delattr(logger, app_logging._LOGGER_STATE_ATTRIBUTE, raising=False)
