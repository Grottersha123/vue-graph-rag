from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import streamlit as st
from pydantic import ValidationError

from mvp_v2.adapters.filesystem.dumps import FilesystemDumpRepository
from mvp_v2.adapters.filesystem.media import FilesystemMediaResolver
from mvp_v2.adapters.ladybug import LadybugAnn, build_database, load_verified_dump
from mvp_v2.adapters.legacy.importer import CsvLegacyImporter
from mvp_v2.adapters.ollama import LocalHttpTransport, OllamaAdapter
from mvp_v2.application.chat import ChatService
from mvp_v2.application.deletion import preview_deletion
from mvp_v2.application.dto.chat import ChatRequest
from mvp_v2.application.dto.deletion import (
    DatabaseIdentity,
    DeletionCommand,
    GraphNodeRef,
)
from mvp_v2.application.dto.importing import LegacyImportResult
from mvp_v2.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    SearchRequest,
)
from mvp_v2.application.ingestion_contracts import (
    EmbeddingRequest,
    IngestionRequest,
    ModelAuthorization,
    ModelIdentity,
    SourceKind,
)
from mvp_v2.application.manifests import DatabaseManifest, DumpManifest
from mvp_v2.application.model_operations import build_embeddings, describe_asset
from mvp_v2.application.relations import RelationKind, RelationService
from mvp_v2.application.retrieval import SearchService
from mvp_v2.application.services.migration import LegacyMigrationService
from mvp_v2.application.services.publication import SnapshotPublicationService
from mvp_v2.bootstrap.ui import UiRuntimeConfig, default_runtime_config
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.presentation.controller import (
    active_database,
    bind_snapshot,
    commit_ingestion,
    configure_ladybug_home,
    confirm_deletion,
    database_is_stale,
    mark_snapshot_dirty,
    parse_runtime_config,
    preview_ingestion,
    remove_staging_directory,
    scan_database_manifests,
    stage_uploads,
)

TAB_LABELS = (
    "Upload",
    "Documents",
    "Relations",
    "Chunks & Embeddings",
    "LadybugDB",
    "Search",
    "Chatbot",
    "Errors/Import report",
)
_SAFE_DB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def run() -> None:
    st.set_page_config(page_title="Vue Graph RAG", layout="wide")
    st.title("Vue Graph RAG — local MVP")
    config = _sidebar()
    _initialize_state()
    tabs = st.tabs(TAB_LABELS)
    for tab, renderer in zip(
        tabs,
        (_upload, _documents, _relations, _chunks, _ladybug, _search, _chat, _errors),
        strict=True,
    ):
        with tab:
            renderer(config)


def _sidebar() -> UiRuntimeConfig | None:
    defaults = default_runtime_config()
    st.sidebar.header("Local runtime paths")
    values = {
        "working_root": st.sidebar.text_input(
            "Working data root", str(defaults.working_root)
        ),
        "dumps_root": st.sidebar.text_input("Dumps", str(defaults.dumps_root)),
        "databases_root": st.sidebar.text_input(
            "Databases", str(defaults.databases_root)
        ),
        "ladybug_home": st.sidebar.text_input(
            "Ladybug HOME", str(defaults.ladybug_home)
        ),
    }
    try:
        return parse_runtime_config(values)
    except ValueError as error:
        st.sidebar.error(str(error))
        st.error("Invalid runtime configuration. Every operation is blocked.")
        return None


def _initialize_state() -> None:
    st.session_state.setdefault("snapshot", GraphSnapshot())
    st.session_state.setdefault("preview", None)
    st.session_state.setdefault("errors", [])
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("active_db", None)
    st.session_state.setdefault("snapshot_dump_run_id", None)
    st.session_state.setdefault("snapshot_dirty", True)


def _blocked(config: UiRuntimeConfig | None) -> bool:
    if config is None:
        st.warning("Blocked: correct the runtime configuration in the sidebar.")
        return True
    return False


def _missing(config: UiRuntimeConfig) -> bool:
    missing = [
        str(path)
        for path in (
            config.working_root,
            config.dumps_root,
            config.databases_root,
            config.ladybug_home,
        )
        if not path.is_dir() or path.is_symlink()
    ]
    if missing:
        st.info("Runtime folders are absent or invalid: " + ", ".join(missing))
        return True
    return False


def _record_error(message: str) -> None:
    st.session_state.errors.append(message)
    st.error(message)


def _upload(config: UiRuntimeConfig | None) -> None:
    st.subheader("Upload")
    blocked = _blocked(config)
    missing = False if config is None else _missing(config)
    snapshot = st.session_state.snapshot
    kind = st.selectbox(
        "Source kind", ("url", "pdf", "markdown", "html", "image_collection")
    )
    source_types = {item.id: item.code for item in snapshot.source_types}
    statuses = {item.id: item.name for item in snapshot.statuses}
    source_type_id = (
        st.selectbox(
            "SourceType",
            tuple(source_types),
            format_func=lambda value: source_types[value],
        )
        if source_types
        else None
    )
    status_id = (
        st.selectbox(
            "Required Status",
            tuple(statuses),
            format_func=lambda value: statuses[value],
        )
        if statuses
        else None
    )
    title = st.text_input("Title")
    external_id = st.text_input("Existing/new identity (external_id)")
    source_url = st.text_input("Public HTTP(S) URL")
    uploads = st.file_uploader("Local input", accept_multiple_files=True)
    valid = bool(
        title.strip()
        and source_type_id
        and status_id
        and (source_url.strip() if kind == "url" else uploads)
    )
    if st.button("Preview", disabled=blocked or missing or not valid):
        try:
            assert config is not None
            input_root, input_path = config.working_root, ""
            if kind != "url":
                input_root = stage_uploads(config.working_root, tuple(uploads or ()))
                input_path = (
                    "." if kind == "image_collection" else tuple(uploads)[0].name
                )
            request = IngestionRequest(
                kind=cast(SourceKind, kind),
                title=title,
                source_type_id=cast(str, source_type_id),
                external_id=external_id,
                status_id=cast(str, status_id),
                source_url=source_url,
                input_path=input_path,
            )
            try:
                st.session_state.preview = preview_ingestion(
                    snapshot,
                    request,
                    working_root=config.working_root,
                    input_root=input_root,
                )
            finally:
                if kind != "url":
                    remove_staging_directory(input_root, config.working_root)
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error("Ingestion preview failed. Check inputs and runtime paths.")
    preview = st.session_state.preview
    if preview is not None:
        st.json(
            {
                "document_id": preview.document_id,
                "version": preview.version,
                "sha256": preview.content_sha256,
                "chunks": len(preview.chunk_texts),
                "assets": len(preview.extracted.assets),
            }
        )
    if st.button("Commit", disabled=blocked or missing or preview is None):
        try:
            assert config is not None
            committed = commit_ingestion(
                snapshot, preview, working_root=config.working_root
            )
            mark_snapshot_dirty(st.session_state, committed)
            st.session_state.preview = None
            st.success(
                "Committed to the session snapshot and working root. No dump or DB was published."  # noqa: E501
            )
        except (ValueError, OSError, RuntimeError):
            _record_error(
                "Ingestion commit failed. No session snapshot change was applied."
            )


def _documents(config: UiRuntimeConfig | None) -> None:
    st.subheader("Documents")
    blocked = _blocked(config)
    snapshot = st.session_state.snapshot
    st.dataframe([item.model_dump() for item in snapshot.documents])
    st.dataframe([item.model_dump() for item in snapshot.document_versions])
    ids = tuple(item.id for item in snapshot.documents)
    selected = st.selectbox("Deletion document ID", ids) if ids else None
    active = _active(config)
    deletion_ready = (
        not blocked
        and selected is not None
        and active is not None
        and st.session_state.get("snapshot_dirty") is False
    )
    if st.button("Preview deletion", disabled=not deletion_ready):
        try:
            assert active is not None
            command = DeletionCommand(
                target=GraphNodeRef(table_name="Document", id=cast(str, selected))
            )
            identity = DatabaseIdentity(
                database_name=active[1].database_name, build_id=active[1].build_id
            )
            impact = preview_deletion(snapshot, command, (identity,))
            st.session_state.deletion_preview = (
                command,
                impact,
                st.session_state.snapshot_dump_run_id,
                active[1].build_id,
            )
        except (ValueError, RuntimeError):
            _record_error("Deletion preview failed.")
    preview_binding = st.session_state.get("deletion_preview")
    if isinstance(preview_binding, tuple) and len(preview_binding) == 4:
        command, impact, dump_run_id, build_id = preview_binding
        st.json(impact.model_dump(mode="json"))
        st.warning("Physical media is never deleted. The previous DB becomes stale.")
        confirmed = st.checkbox(
            "Confirm exact deletion preview and replacement DB build", value=False
        )
        still_exact = bool(
            active is not None
            and dump_run_id == st.session_state.get("snapshot_dump_run_id")
            and build_id == active[1].build_id
        )
        if st.button(
            "Confirm deletion",
            disabled=not confirmed or not still_exact or config is None,
        ):
            try:
                assert config is not None and active is not None
                configure_ladybug_home(config)
                execution = confirm_deletion(
                    snapshot=snapshot,
                    command=command,
                    confirmation=impact,
                    config=config,
                    active=active,
                )
                dump_path = config.dumps_root / (
                    f"dump-{execution.dump.dump_number:06d}"
                )
                bind_snapshot(
                    st.session_state, execution.snapshot, execution.dump, dump_path
                )
                if execution.result.status == "completed":
                    assert execution.database_path is not None
                    assert execution.database is not None
                    st.session_state.active_db = (
                        execution.database_path,
                        execution.database,
                    )
                    st.success("Deletion published and replacement DB selected.")
                else:
                    st.session_state.active_db = None
                    st.warning(
                        "Deletion published. Replacement DB is pending; "
                        "old DB is stale."
                    )
                st.session_state.pop("deletion_preview", None)
            except (ValueError, OSError, RuntimeError):
                _record_error("Deletion failed before a safe session transition.")


def _relations(config: UiRuntimeConfig | None) -> None:
    st.subheader("Relations")
    blocked = _blocked(config)
    snapshot = st.session_state.snapshot
    kind = RelationKind(
        st.selectbox("Relation type", tuple(item.value for item in RelationKind))
    )
    source_id, target_id = (
        st.text_input("Exact source ID"),
        st.text_input("Exact target ID"),
    )
    col1, col2 = st.columns(2)
    for label, action, column in (("Add", "add", col1), ("Delete", "delete", col2)):
        if column.button(
            label, disabled=blocked or not source_id.strip() or not target_id.strip()
        ):
            try:
                service = RelationService()
                updated = (service.add if action == "add" else service.delete)(
                    snapshot, kind, source_id, target_id
                )
                mark_snapshot_dirty(st.session_state, updated)
                st.success(f"Relation {action} completed in the session snapshot.")
            except (ValueError, RuntimeError):
                _record_error(
                    f"Relation {action} failed. Check exact IDs and graph invariants."
                )
    st.dataframe(
        [
            {"kind": name, **row.model_dump(by_alias=True)}
            for name, rows in (
                ("LINKS_TO", snapshot.links_to),
                ("DESCRIBES", snapshot.describes),
                ("FOR_ROLE", snapshot.for_roles),
            )
            for row in rows
        ]
    )


def _model_fields(prefix: str = "") -> tuple[str, str, str, int]:
    base = st.text_input(
        "Ollama base URL", "http://127.0.0.1:11434", key=f"{prefix}base"
    )
    st.text_input("Provider", "ollama", disabled=True, key=f"{prefix}provider")
    model = st.text_input("Exact embedding model", key=f"{prefix}embedding_model")
    version = st.text_input(
        "Exact embedding model version", key=f"{prefix}embedding_version"
    )
    dimension = st.number_input(
        "Embedding dimension", min_value=1, value=768, step=1, key=f"{prefix}dimension"
    )
    return base, model.strip(), version.strip(), int(dimension)


def _chunks(config: UiRuntimeConfig | None) -> None:
    st.subheader("Chunks & Embeddings")
    blocked = _blocked(config)
    snapshot = st.session_state.snapshot
    st.dataframe([item.model_dump() for item in snapshot.chunks])
    st.dataframe([item.model_dump() for item in snapshot.assets])
    st.dataframe(
        [
            {
                **item.model_dump(),
                "history": "current" if item.is_current else "historical",
            }
            for item in snapshot.embeddings
        ]
    )
    base, model, version, dimension = _model_fields("chunks_")
    token = st.text_input("Exact embedding generation token")
    batch = int(
        st.number_input("Embedding batch size", min_value=1, max_value=256, value=16)
    )
    allow_embedding = st.checkbox("Authorize this embedding operation", value=False)
    ready = bool(model and version and token and allow_embedding and not blocked)
    for label, mode in (
        ("Build missing embeddings", "missing"),
        ("Rebuild all embeddings", "all"),
    ):
        if st.button(label, disabled=not ready):
            try:
                request = EmbeddingRequest(
                    identity=ModelIdentity(
                        provider="ollama",
                        model=model,
                        model_version=version,
                        dimension=dimension,
                    ),
                    authorization=ModelAuthorization(allow=True, operation="embedding"),
                    mode=cast(Any, mode),
                    generation_token=token,
                    created_at=datetime.now(UTC),
                    batch_size=batch,
                )
                updated = build_embeddings(
                    snapshot, request, OllamaAdapter(LocalHttpTransport(base))
                )
                mark_snapshot_dirty(st.session_state, updated)
                st.success("Session embedding snapshot updated.")
            except (ValueError, OSError, RuntimeError, ValidationError):
                _record_error(
                    "Embedding operation failed safely; the snapshot was not changed."
                )
    assets = {item.id: item for item in snapshot.assets}
    asset_id = (
        st.selectbox(
            "Asset", tuple(assets), format_func=lambda value: assets[value].name
        )
        if assets
        else None
    )
    vision_model = st.text_input("Exact vision model")
    vision_version = st.text_input("Exact vision model version")
    allow_vision = st.checkbox("Authorize this vision operation", value=False)
    if st.button(
        "Describe selected asset",
        disabled=blocked
        or asset_id is None
        or not vision_model.strip()
        or not vision_version.strip()
        or not allow_vision,
    ):
        try:
            assert config is not None and asset_id is not None
            asset = assets[asset_id]
            image = FilesystemMediaResolver(config.working_root).read_verified(
                asset.local_path, asset.sha256
            )
            updated = describe_asset(
                snapshot,
                asset_id,
                image,
                ModelAuthorization(allow=True, operation="vision_description"),
                model=vision_model.strip(),
                vision=OllamaAdapter(LocalHttpTransport(base)),
            )
            mark_snapshot_dirty(st.session_state, updated)
            st.success(
                f"Asset description updated with {vision_model.strip()} ({vision_version.strip()})."  # noqa: E501
            )
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error(
                "Vision operation failed safely; media or model verification failed."
            )


def _dump_paths(root: Path) -> tuple[Path, ...]:
    if not root.is_dir() or root.is_symlink():
        return ()
    return tuple(
        sorted(
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and path.name.startswith("dump-")
            and len(path.name) == 11
            and path.name[5:].isdigit()
        )
    )


def _ladybug(config: UiRuntimeConfig | None) -> None:
    st.subheader("LadybugDB")
    blocked = _blocked(config)
    missing = False if config is None else _missing(config)
    dumps = () if config is None else _dump_paths(config.dumps_root)
    selected = (
        st.selectbox(
            "Verified dump candidate", dumps, format_func=lambda path: path.name
        )
        if dumps
        else None
    )
    if st.button("Load selected canonical dump", disabled=blocked or selected is None):
        try:
            assert selected is not None
            dump_manifest, snapshot = load_verified_dump(selected)
            bind_snapshot(st.session_state, snapshot, dump_manifest, selected)
            st.session_state.active_db = None
            st.success("Verified dump loaded into session.")
        except (ValueError, OSError, RuntimeError):
            _record_error("Dump verification or loading failed.")
    confirm = st.checkbox(
        "Confirm publish current snapshot as next immutable dump", value=False
    )
    if st.button(
        "Publish current snapshot", disabled=blocked or missing or not confirm
    ):
        try:
            assert config is not None
            dump_manifest = SnapshotPublicationService(
                FilesystemDumpRepository()
            ).publish(
                dumps_root=config.dumps_root,
                snapshot=st.session_state.snapshot,
                published_at=datetime.now(UTC),
                media_resolver=FilesystemMediaResolver(config.working_root),
            )
            dump_path = config.dumps_root / f"dump-{dump_manifest.dump_number:06d}"
            bind_snapshot(
                st.session_state, st.session_state.snapshot, dump_manifest, dump_path
            )
            st.session_state.active_db = None
            st.success("Current snapshot published as a new immutable dump.")
        except (ValueError, OSError, RuntimeError):
            _record_error(
                "Snapshot publication failed validation or filesystem checks."
            )
    artifacts = () if config is None else scan_database_manifests(config.databases_root)
    current_dump = cast(DumpManifest | None, st.session_state.get("dump_manifest"))
    for path, db_manifest in artifacts:
        st.write(
            {
                "artifact": path.name,
                "build_id": db_manifest.build_id,
                "counts": {x.table: x.count for x in db_manifest.row_counts},
                "stale": database_is_stale(db_manifest, current_dump),
                "embedding": db_manifest.embedding_space.model_dump(),
                "vision": [
                    db_manifest.vision_provider,
                    db_manifest.vision_model,
                    db_manifest.vision_model_version,
                ],
            }
        )
    selectable = tuple(
        (p, m) for p, m in artifacts if not database_is_stale(m, current_dump)
    )
    active = (
        st.selectbox(
            "Active verified current DB",
            selectable,
            format_func=lambda item: item[0].name,
        )
        if selectable
        else None
    )
    if st.button("Use selected DB", disabled=blocked or active is None):
        st.session_state.active_db = active
    name = st.text_input("Safe new database name")
    base, emodel, eversion, dimension = _model_fields("db_")
    del base
    vmodel, vversion = (
        st.text_input("DB vision model"),
        st.text_input("DB vision model version"),
    )
    build_confirm = st.checkbox("Confirm exact new DB build", value=False)
    build_ready = bool(
        not blocked
        and not missing
        and selected
        and st.session_state.get("snapshot_dirty") is False
        and selected == st.session_state.get("selected_dump")
        and _SAFE_DB_NAME.fullmatch(name)
        and emodel
        and eversion
        and vmodel.strip()
        and vversion.strip()
        and build_confirm
    )
    if st.button("Save as new DB", disabled=not build_ready):
        try:
            assert config is not None and selected is not None
            configure_ladybug_home(config)
            target = config.databases_root / f"{name}.lbdb"
            db_manifest = build_database(
                dump_path=selected,
                target=target,
                database_name=name,
                space=EmbeddingSpaceIdentity(
                    provider="ollama",
                    model=emodel,
                    model_version=eversion,
                    dimension=dimension,
                ),
                vision_provider="ollama",
                vision_model=vmodel.strip(),
                vision_model_version=vversion.strip(),
            )
            st.session_state.active_db = (target, db_manifest)
            st.success("New database built, verified, and selected.")
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error("Database build failed safely; no active DB was changed.")
    if config is not None:
        st.code(
            f'/tmp/vue-graph-rag-mvp-v2/.venv/bin/python -m mvp_v2.bootstrap.provision_vector --home "{config.ladybug_home}" --yes'  # noqa: E501
        )


def _active(config: UiRuntimeConfig | None) -> tuple[Path, DatabaseManifest] | None:
    return active_database(st.session_state, config)


def _with_search(
    config: UiRuntimeConfig,
    active: tuple[Path, DatabaseManifest],
    request: SearchRequest,
) -> tuple[Any, ...]:
    path, manifest = active
    configure_ladybug_home(config)
    ladybug = importlib.import_module("ladybug")
    database = connection = None
    try:
        database = ladybug.Database(str(path / "database.lbug"))
        connection = ladybug.Connection(database)
        service = SearchService(
            OllamaAdapter(LocalHttpTransport(st.session_state.search_base)),
            LadybugAnn(connection, manifest),
            manifest,
            st.session_state.snapshot,
        )
        return service.search(request)
    finally:
        if connection is not None:
            connection.close()
        if database is not None:
            database.close()


def _search_fields(prefix: str) -> tuple[str, int, bool]:
    base = st.text_input(
        "Search Ollama base URL", "http://127.0.0.1:11434", key=f"{prefix}_base"
    )
    limit = int(
        st.number_input(
            "Evidence limit", min_value=1, max_value=100, value=8, key=f"{prefix}_limit"
        )
    )
    allow = st.checkbox(
        "Authorize question embedding", value=False, key=f"{prefix}_allow"
    )
    return base, limit, allow


def _search(config: UiRuntimeConfig | None) -> None:
    st.subheader("Search")
    active = _active(config)
    question = st.text_input("Search question")
    base, limit, allow = _search_fields("search")
    if st.button(
        "Search evidence",
        disabled=config is None or active is None or not question.strip() or not allow,
    ):
        try:
            assert config is not None and active is not None
            st.session_state.search_base = base
            manifest = active[1]
            request = SearchRequest(
                question=question,
                space=manifest.embedding_space,
                purposes=(
                    EmbeddingPurpose.CHUNK_RETRIEVAL,
                    EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
                ),
                limit=limit,
                authorization=ModelAuthorization(allow=True, operation="embedding"),
            )
            st.session_state.search_evidence = _with_search(config, active, request)
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error(
                "Search failed safely; verify DB, model identity, and authorization."
            )
    for number, item in enumerate(st.session_state.get("search_evidence", ()), 1):
        st.write(f"Evidence {number}")
        st.write(item.text)
        st.write(
            f"[{number}] {item.document_title} — document={item.document_id}, version={item.version}, embedding={item.embedding_id}, score={item.score:.6f}, source={item.source_url or item.local_path}"  # noqa: E501
        )


def _chat(config: UiRuntimeConfig | None) -> None:
    st.subheader("Chatbot")
    if st.button("Clear chat history"):
        st.session_state.chat_history = []
    active = _active(config)
    base, limit, allow_embed = _search_fields("chat")
    model, version = (
        st.text_input("Exact chat model"),
        st.text_input("Exact chat model version"),
    )
    allow_generation = st.checkbox("Authorize answer generation", value=False)
    question = st.chat_input(
        "Ask from retrieved evidence", disabled=config is None or active is None
    )
    if question:
        if not (allow_embed and allow_generation and model.strip() and version.strip()):
            _record_error(
                "Chat requires separate embedding and generation authorizations and exact model versions."  # noqa: E501
            )
        else:
            try:
                assert config is not None and active is not None
                path, manifest = active
                configure_ladybug_home(config)
                ladybug = importlib.import_module("ladybug")
                database = connection = None
                try:
                    database = ladybug.Database(str(path / "database.lbug"))
                    connection = ladybug.Connection(database)
                    adapter = OllamaAdapter(LocalHttpTransport(base))
                    search_request = SearchRequest(
                        question=question,
                        space=manifest.embedding_space,
                        purposes=(
                            EmbeddingPurpose.CHUNK_RETRIEVAL,
                            EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
                        ),
                        limit=limit,
                        authorization=ModelAuthorization(
                            allow=True, operation="embedding"
                        ),
                    )
                    service = ChatService(
                        SearchService(
                            adapter,
                            LadybugAnn(connection, manifest),
                            manifest,
                            st.session_state.snapshot,
                        ),
                        adapter,
                    )
                    answer = service.answer(
                        ChatRequest(
                            question=question,
                            search=search_request,
                            model=model.strip(),
                            model_version=version.strip(),
                            authorization=ModelAuthorization(
                                allow=True, operation="generation"
                            ),
                        )
                    )
                finally:
                    if connection is not None:
                        connection.close()
                    if database is not None:
                        database.close()
                st.session_state.chat_history.append((question, answer))
            except (ValueError, OSError, RuntimeError, ValidationError):
                _record_error("Chat failed safely; no unverified answer was added.")
    for prior_question, answer in st.session_state.chat_history:
        st.write(f"Question: {prior_question}")
        st.write(answer.answer)
        for citation in answer.citations:
            st.write(
                f"[{citation.number}] {citation.document_title} — document={citation.document_id}, version={citation.version}, evidence={citation.evidence_id}, source={citation.source_url or citation.local_path}"  # noqa: E501
            )


def _errors(config: UiRuntimeConfig | None) -> None:
    st.subheader("Errors/Import report")
    blocked = _blocked(config)
    st.write(st.session_state.errors or "No operation errors.")
    available = (
        config is not None
        and config.working_root.is_dir()
        and not config.working_root.is_symlink()
    )
    if st.button("Preview legacy import", disabled=blocked or not available):
        try:
            assert config is not None
            service = LegacyMigrationService(
                importer=CsvLegacyImporter(), dump_repository=FilesystemDumpRepository()
            )
            st.session_state.import_result = service.preview(
                runtime_data_root=config.working_root
            )
        except (ValueError, OSError, RuntimeError):
            _record_error("Legacy import preview failed. No data was published.")
    result = st.session_state.get("import_result")
    if result is not None:
        st.json(result.report.model_dump(mode="json"))
    approved = st.text_input(
        "Exact approved report_id", value="", placeholder="Not pre-approved"
    )
    approve = st.checkbox("I reviewed the exact report and snapshot hash", value=False)
    if st.button(
        "Approve and publish legacy import",
        disabled=blocked or result is None or not approve or not approved.strip(),
    ):
        try:
            assert config is not None
            service = LegacyMigrationService(
                importer=CsvLegacyImporter(), dump_repository=FilesystemDumpRepository()
            )
            typed = cast(LegacyImportResult, result)
            manifest = service.publish(
                result=typed,
                approved_report_id=approved,
                dumps_root=config.dumps_root,
                media_resolver=FilesystemMediaResolver(config.working_root),
                published_at=datetime.now(UTC),
            )
            dump_path = config.dumps_root / f"dump-{manifest.dump_number:06d}"
            bind_snapshot(st.session_state, typed.snapshot, manifest, dump_path)
            st.session_state.active_db = None
            st.success("Approved legacy snapshot published and loaded into session.")
        except (ValueError, OSError, RuntimeError):
            _record_error(
                "Legacy approval or publication failed. Check exact report ID and report facts."  # noqa: E501
            )
