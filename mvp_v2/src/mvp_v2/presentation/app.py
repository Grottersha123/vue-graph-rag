from __future__ import annotations

import importlib
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, TypeVar, cast

import streamlit as st
from pydantic import ValidationError

from mvp_v2.adapters.filesystem.dumps import FilesystemDumpRepository
from mvp_v2.adapters.filesystem.media import FilesystemMediaResolver
from mvp_v2.adapters.ladybug import LadybugAnn, build_database, load_verified_dump
from mvp_v2.adapters.legacy.importer import CsvLegacyImporter
from mvp_v2.adapters.ollama import LocalHttpTransport, OllamaAdapter
from mvp_v2.application.chat import MAX_EVIDENCE_ITEMS, ChatService
from mvp_v2.application.chat_export import ChatTurnProvenance, render_chat_markdown
from mvp_v2.application.chunk_backfill import build_missing_chunks
from mvp_v2.application.deletion import preview_deletion
from mvp_v2.application.document_editing import rename_document
from mvp_v2.application.document_view import (
    DocumentSummary,
    build_document_summaries,
    build_document_view,
)
from mvp_v2.application.dto.chat import ChatRequest
from mvp_v2.application.dto.deletion import (
    DeletionCommand,
    DeletionImpact,
    GraphNodeRef,
)
from mvp_v2.application.dto.importing import LegacyImportResult
from mvp_v2.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    SearchRequest,
)
from mvp_v2.application.dump_restamp import apply_dump_restamp, plan_dump_restamp
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
from mvp_v2.application.title_backfill import fill_missing_document_titles
from mvp_v2.bootstrap.ui import UiRuntimeConfig, default_runtime_config
from mvp_v2.domain.graph import Asset
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.presentation.controller import (
    active_database,
    bind_snapshot,
    commit_ingestion,
    configure_ladybug_home,
    confirm_file_deletion,
    database_is_stale,
    mark_snapshot_dirty,
    parse_runtime_config,
    preview_ingestion,
    primary_upload_name,
    remove_staging_directory,
    scan_database_manifests,
    stage_uploads,
)
from mvp_v2.presentation.logging import (
    application_log_path,
    configure_application_logging,
    read_log_tail,
    record_operation_error,
    record_user_action,
    run_logged_operation,
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
_ResultT = TypeVar("_ResultT")


def _environment_text(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _environment_dimension() -> int:
    raw = os.environ.get("MVP_V2_EMBEDDING_DIMENSION", "768")
    try:
        value = int(raw)
    except ValueError:
        return 768
    return value if value > 0 else 768


def _asset_label(asset: Asset) -> str:
    return asset.caption.strip() or asset.local_path


def _action_button(label: str, *, disabled: bool = False) -> bool:
    clicked = st.button(label, disabled=disabled)
    if clicked:
        record_user_action(label)
    return clicked


def _run_visible_operation(
    operation: str, message: str, action: Callable[[], _ResultT]
) -> _ResultT:
    with st.spinner(message, show_time=True):
        return run_logged_operation(operation, action)


def run() -> None:
    try:
        configure_application_logging()
    except (OSError, RuntimeError):
        pass
    st.set_page_config(page_title="Vue Graph RAG", layout="wide")
    st.title("Vue Graph RAG — local MVP")
    config = _sidebar()
    _initialize_state()
    _render_session_status(config)
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
    st.session_state.setdefault("restamp_plan", None)


def _render_session_status(config: UiRuntimeConfig | None) -> None:
    st.sidebar.divider()
    st.sidebar.subheader("Current session")
    dump_manifest = st.session_state.get("dump_manifest")
    snapshot_dirty = st.session_state.get("snapshot_dirty") is not False
    if isinstance(dump_manifest, DumpManifest):
        label = f"dump-{dump_manifest.dump_number:06d}"
        if snapshot_dirty:
            st.sidebar.warning(f"Base dump: {label} — unpublished changes")
        else:
            st.sidebar.success(f"Loaded dump: {label}")
        st.sidebar.caption(f"run_id: {dump_manifest.run_id[:12]}…")
    else:
        st.sidebar.info("No canonical dump is loaded.")
    active = _active(config)
    if active is None:
        st.sidebar.caption("Active DB: none")
    else:
        st.sidebar.caption(
            f"Active DB: {active[1].database_name} ({active[1].build_id[:12]}…)"
        )


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
    error = sys.exception()
    if error is None:
        display = message
    else:
        record_operation_error(message)
        detail = str(error).replace("\n", " ").replace("\r", " ")[:500]
        display = f"{message} [{type(error).__name__}: {detail}]"
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    entry = f"{timestamp} {display}"
    st.session_state.errors.append(entry)
    st.error(display)


def _preferred_option_index(options: dict[str, str], preferred: str) -> int:
    preferred_key = preferred.casefold()
    for index, (identifier, label) in enumerate(options.items(), start=1):
        if identifier.casefold() == preferred_key or label.casefold() == preferred_key:
            return index
    return 0


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
    processes = {item.id: item.name for item in snapshot.processes}
    roles = {item.id: item.name for item in snapshot.roles}
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
    process_id = st.selectbox(
        "Process (optional)",
        ("", *processes),
        index=_preferred_option_index(processes, "onboarding"),
        format_func=lambda value: processes.get(value, "Not selected"),
    )
    role_id = st.selectbox(
        "Role (optional)",
        ("", *roles),
        index=_preferred_option_index(roles, "student"),
        format_func=lambda value: roles.get(value, "Not selected"),
    )
    title = st.text_input("Title")
    external_id = st.text_input("Existing/new identity (external_id)")
    source_url_label = (
        "Public HTTP(S) URL (required)"
        if kind == "url"
        else "Source URL (optional provenance)"
    )
    source_url = st.text_input(source_url_label)
    uploads = (
        st.file_uploader("Local input", accept_multiple_files=True)
        if kind != "url"
        else None
    )
    valid = bool(
        title.strip()
        and source_type_id
        and status_id
        and (source_url.strip() if kind == "url" else uploads)
    )
    if _action_button("Preview", disabled=blocked or missing or not valid):
        try:
            assert config is not None
            input_root, input_path = config.working_root, ""
            if kind != "url":
                local_uploads = tuple(uploads or ())
                input_path = primary_upload_name(cast(SourceKind, kind), local_uploads)
                input_root = stage_uploads(config.working_root, local_uploads)
            request = IngestionRequest(
                kind=cast(SourceKind, kind),
                title=title,
                source_type_id=cast(str, source_type_id),
                external_id=external_id,
                status_id=cast(str, status_id),
                source_url=source_url,
                input_path=input_path,
                process_id=process_id or None,
                role_id=role_id or None,
            )
            try:
                st.session_state.preview = _run_visible_operation(
                    "ingestion_preview",
                    "Подготовка документа… Подождите.",
                    lambda: preview_ingestion(
                        snapshot,
                        request,
                        working_root=config.working_root,
                        input_root=input_root,
                    ),
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
        if preview.extracted.downloadable_urls:
            st.markdown("#### Potential downloads")
            for url in preview.extracted.downloadable_urls:
                st.link_button(url, url)
    if _action_button("Commit", disabled=blocked or missing or preview is None):
        try:
            assert config is not None
            committed = _run_visible_operation(
                "ingestion_commit",
                "Загрузка документа… Подождите.",
                lambda: commit_ingestion(
                    snapshot, preview, working_root=config.working_root
                ),
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


def _render_document_preview(snapshot: GraphSnapshot, summary: DocumentSummary) -> None:
    if summary.version is None:
        with st.expander(f"Preview: {summary.document.title}", expanded=False):
            st.warning("This document has no linked current version in the snapshot.")
        return
    try:
        view = build_document_view(snapshot, summary.document.id)
        with st.expander(f"Preview: {summary.document.title}", expanded=False):
            st.json(
                {
                    "document_id": summary.document.id,
                    "version": view.version.version,
                    "format": summary.document.content_format,
                    "source": summary.document.source_url or view.version.local_path,
                    "chunks": view.chunk_count,
                    "assets": len(view.assets),
                }
            )
            if view.content:
                st.code(view.content, language="markdown", wrap_lines=True)
            else:
                st.info("This document has no indexed text content.")
            if view.assets:
                st.markdown("#### Assets")
                st.json([asset.model_dump(mode="json") for asset in view.assets])
    except (TypeError, ValueError):
        _record_error("Selected document could not be previewed.")


def _render_document_actions(
    *, blocked: bool, snapshot: GraphSnapshot, summary: DocumentSummary
) -> None:
    document = summary.document
    st.markdown("#### Selected document actions")
    title_column, source_column = st.columns(2)
    with title_column:
        edited_title = st.text_input(
            "Document title",
            value=document.title,
            key=f"document_title_{document.id}",
        )
        if _action_button("Save title", disabled=blocked or not edited_title.strip()):
            try:
                updated = rename_document(snapshot, document.id, edited_title)
                if updated is snapshot:
                    st.info("Document title is unchanged.")
                else:
                    mark_snapshot_dirty(st.session_state, updated)
                    st.success("Document title updated in the session snapshot.")
            except ValueError:
                _record_error("Document title could not be updated.")
    with source_column:
        if document.source_url.startswith(("http://", "https://")):
            st.link_button("Open source URL", document.source_url)
        elif document.source_url:
            st.caption(f"Source: {document.source_url}")
        else:
            st.caption("No source URL saved for this document.")


def _render_title_backfill(*, blocked: bool, snapshot: GraphSnapshot) -> None:
    st.divider()
    st.caption(
        "Fix generated titles replaces 64-character SHA titles from content headings. "
        "It changes only the in-memory snapshot; it does not create a dump or rebuild "
        "the database."
    )
    if not _action_button(
        "Fix generated titles", disabled=blocked or not snapshot.documents
    ):
        return
    try:
        updated, renamed = _run_visible_operation(
            "title_backfill",
            "Заполнение названий документов… Подождите.",
            lambda: fill_missing_document_titles(st.session_state.snapshot),
        )
        if renamed:
            mark_snapshot_dirty(st.session_state, updated)
            st.success(
                f"Renamed {renamed} documents from content headings. "
                "Next: LadybugDB → Publish current snapshot, then rebuild the DB."
            )
        else:
            st.info("No generated SHA titles found.")
    except (ValueError, OSError, RuntimeError):
        _record_error("Title backfill failed safely; no titles were changed.")


def _execute_document_deletion(
    *,
    config: UiRuntimeConfig,
    snapshot: GraphSnapshot,
    command: DeletionCommand,
    impact: DeletionImpact,
) -> None:
    try:
        outcome = _run_visible_operation(
            "file_deletion",
            "Публикация нового dump и удаление файлов… Подождите.",
            lambda: confirm_file_deletion(
                snapshot=snapshot,
                command=command,
                confirmation=impact,
                config=config,
            ),
        )
        dump_path = config.dumps_root / f"dump-{outcome.dump.dump_number:06d}"
        bind_snapshot(st.session_state, outcome.snapshot, outcome.dump, dump_path)
        st.session_state.active_db = None
        st.session_state.pop("deletion_preview", None)
        if outcome.failed_paths:
            st.session_state.document_notice = (
                "warning",
                "Deletion was published, but some physical files could not be "
                f"deleted: {', '.join(outcome.failed_paths)}",
            )
        else:
            st.session_state.document_notice = (
                "success",
                "Deletion published as a new dump. "
                f"Removed {len(outcome.deleted_paths)} physical files; "
                f"{len(outcome.missing_paths)} were already absent. "
                "Rebuild the database when ready.",
            )
        st.rerun()
    except (ValueError, OSError, RuntimeError):
        _record_error("Deletion failed before a safe session transition.")


def _working_root_identity(
    config: UiRuntimeConfig | None,
) -> tuple[str, int, int] | None:
    if config is None or config.working_root.is_symlink():
        return None
    try:
        root = config.working_root.resolve(strict=True)
        root_stat = root.stat()
    except OSError:
        return None
    if not root.is_dir():
        return None
    return str(root), root_stat.st_dev, root_stat.st_ino


def _render_deletion_confirmation(
    *,
    config: UiRuntimeConfig | None,
    snapshot: GraphSnapshot,
    selected_document_id: str | None,
) -> None:
    preview_binding = st.session_state.get("deletion_preview")
    if not isinstance(preview_binding, tuple) or len(preview_binding) != 4:
        return
    command, impact, dump_run_id, working_root_identity = preview_binding
    if command.target.id != selected_document_id:
        return
    with st.expander("Deletion preview", expanded=False):
        st.json(impact.model_dump(mode="json"))
        st.warning(
            "A new canonical dump will be published. The listed document and asset "
            "files will then be permanently deleted. The database must be rebuilt "
            "later."
        )
        confirmed = st.checkbox(
            "Confirm graph rows and listed physical files deletion", value=False
        )
        still_exact = bool(
            dump_run_id == st.session_state.get("snapshot_dump_run_id")
            and working_root_identity == _working_root_identity(config)
            and st.session_state.get("snapshot_dirty") is False
        )
        if _action_button(
            "Confirm deletion",
            disabled=not confirmed or not still_exact or config is None,
        ):
            assert config is not None
            _execute_document_deletion(
                config=config,
                snapshot=snapshot,
                command=command,
                impact=impact,
            )


def _deletion_block_reason(
    *, blocked: bool, selected_document_id: str | None, snapshot_dirty: bool
) -> str | None:
    if blocked:
        return "Deletion is blocked until the runtime configuration is valid."
    if selected_document_id is None:
        return "Select one document to enable deletion preview."
    if snapshot_dirty:
        return "Publish or reload the snapshot before deleting a document."
    return None


def _render_document_deletion(
    *,
    config: UiRuntimeConfig | None,
    blocked: bool,
    snapshot: GraphSnapshot,
    selected_document_id: str | None,
) -> None:
    block_reason = _deletion_block_reason(
        blocked=blocked,
        selected_document_id=selected_document_id,
        snapshot_dirty=st.session_state.get("snapshot_dirty") is not False,
    )
    if block_reason is not None:
        st.caption(block_reason)
    if _action_button("Preview deletion", disabled=block_reason is not None):
        try:
            assert config is not None and selected_document_id is not None
            command = DeletionCommand(
                target=GraphNodeRef(table_name="Document", id=selected_document_id)
            )
            impact = preview_deletion(snapshot, command)
            st.session_state.deletion_preview = (
                command,
                impact,
                st.session_state.snapshot_dump_run_id,
                _working_root_identity(config),
            )
        except (ValueError, RuntimeError):
            _record_error("Deletion preview failed.")
    _render_deletion_confirmation(
        config=config,
        snapshot=snapshot,
        selected_document_id=selected_document_id,
    )


def _documents(config: UiRuntimeConfig | None) -> None:
    st.subheader("Documents")
    document_notice = st.session_state.pop("document_notice", None)
    if isinstance(document_notice, tuple) and len(document_notice) == 2:
        notice_level, notice_message = document_notice
        if notice_level == "success":
            st.success(notice_message)
        else:
            st.warning(notice_message)
    blocked = _blocked(config)
    snapshot = st.session_state.snapshot
    try:
        summaries = build_document_summaries(snapshot)
    except ValueError:
        _record_error("Documents could not be listed from the current snapshot.")
        return

    document_event = st.dataframe(
        [
            {
                "id": item.document.id,
                "title": item.document.title,
                "format": item.document.content_format,
                "source": item.document.source_url
                or ("" if item.version is None else item.version.local_path),
                "version": None if item.version is None else item.version.version,
                "chunks": item.chunk_count,
                "assets": item.asset_count,
                "active": item.document.is_active,
            }
            for item in summaries
        ],
        key="documents_table",
        on_select="rerun",
        selection_mode="single-row",
        width="stretch",
    )
    selected_rows = cast(Any, document_event).selection.rows
    selected_summary = None
    if selected_rows:
        try:
            selected_summary = summaries[int(selected_rows[0])]
        except (IndexError, TypeError, ValueError):
            _record_error("Selected document could not be found.")

    if selected_summary is None:
        st.info("Select one document to preview or change it.")
    else:
        _render_document_preview(snapshot, selected_summary)
        _render_document_actions(
            blocked=blocked, snapshot=snapshot, summary=selected_summary
        )
    _render_title_backfill(blocked=blocked, snapshot=snapshot)
    _render_document_deletion(
        config=config,
        blocked=blocked,
        snapshot=snapshot,
        selected_document_id=(
            None if selected_summary is None else selected_summary.document.id
        ),
    )


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
        "Ollama base URL",
        _environment_text("MVP_V2_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        key=f"{prefix}base",
    )
    st.text_input("Provider", "ollama", disabled=True, key=f"{prefix}provider")
    model = st.text_input(
        "Exact embedding model",
        _environment_text("MVP_V2_EMBEDDING_MODEL"),
        key=f"{prefix}embedding_model",
    )
    version = st.text_input(
        "Exact embedding model version",
        _environment_text("MVP_V2_EMBEDDING_MODEL_VERSION"),
        key=f"{prefix}embedding_version",
    )
    dimension = st.number_input(
        "Embedding dimension",
        min_value=1,
        value=_environment_dimension(),
        step=1,
        key=f"{prefix}dimension",
    )
    return base, model.strip(), version.strip(), int(dimension)


def _chunks(config: UiRuntimeConfig | None) -> None:
    st.subheader("Chunks & Embeddings")
    blocked = _blocked(config)
    snapshot = st.session_state.snapshot
    if _action_button("Create missing text chunks", disabled=blocked):
        try:
            assert config is not None
            updated = _run_visible_operation(
                "chunk_generation",
                "Создание chunks… Подождите.",
                lambda: build_missing_chunks(
                    snapshot, FilesystemMediaResolver(config.working_root)
                ),
            )
            created = len(updated.chunks) - len(snapshot.chunks)
            if created:
                mark_snapshot_dirty(st.session_state, updated)
                st.success(f"Created {created} missing text chunks.")
                snapshot = updated
            else:
                st.info("No missing text chunks were found.")
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error(
                "Chunk generation failed safely; the snapshot was not changed."
            )
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
        if _action_button(label, disabled=not ready):
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
                updated = _run_visible_operation(
                    "embedding_generation",
                    "Создание embeddings… Подождите.",
                    partial(
                        build_embeddings,
                        snapshot,
                        request,
                        OllamaAdapter(LocalHttpTransport(base)),
                    ),
                )
                if updated == snapshot:
                    st.info("No eligible missing embeddings were found.")
                else:
                    mark_snapshot_dirty(st.session_state, updated)
                    st.success("Session embedding snapshot updated.")
            except (ValueError, OSError, RuntimeError, ValidationError):
                _record_error(
                    "Embedding operation failed safely; the snapshot was not changed."
                )
    assets = {item.id: item for item in snapshot.assets}
    asset_id = (
        st.selectbox(
            "Asset",
            tuple(assets),
            format_func=lambda value: _asset_label(assets[value]),
        )
        if assets
        else None
    )
    vision_model = st.text_input(
        "Exact vision model", _environment_text("MVP_V2_VISION_MODEL")
    )
    vision_version = st.text_input(
        "Exact vision model version", _environment_text("MVP_V2_VISION_MODEL_VERSION")
    )
    allow_vision = st.checkbox("Authorize this vision operation", value=False)
    if _action_button(
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
            updated = _run_visible_operation(
                "image_description",
                "Создание описания изображения… Подождите.",
                lambda: describe_asset(
                    snapshot,
                    asset_id,
                    image,
                    ModelAuthorization(allow=True, operation="vision_description"),
                    model=vision_model.strip(),
                    vision=OllamaAdapter(LocalHttpTransport(base)),
                ),
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
    loaded_dump = st.session_state.get("selected_dump")
    selected_index = dumps.index(loaded_dump) if loaded_dump in dumps else 0
    if st.session_state.get("snapshot_dirty") is True:
        st.warning(
            "The session contains unpublished changes. Publishing creates the next "
            "immutable dump; it does not overwrite the loaded dump."
        )
    selected = (
        st.selectbox(
            "Verified dump candidate",
            dumps,
            index=selected_index,
            format_func=lambda path: path.name,
        )
        if dumps
        else None
    )
    if _action_button(
        "Load selected canonical dump", disabled=blocked or selected is None
    ):
        try:
            assert selected is not None
            dump_manifest, snapshot = _run_visible_operation(
                "dump_loading",
                "Загрузка snapshot… Подождите.",
                lambda: load_verified_dump(selected),
            )
            bind_snapshot(st.session_state, snapshot, dump_manifest, selected)
            st.session_state.active_db = None
            st.toast("Verified dump loaded into session.")
            st.rerun()
        except (ValueError, OSError, RuntimeError):
            _record_error("Dump verification or loading failed.")
    confirm = st.checkbox(
        "Confirm publish current snapshot as next immutable dump", value=False
    )
    if _action_button(
        "Publish current snapshot", disabled=blocked or missing or not confirm
    ):
        try:
            assert config is not None
            dump_manifest = _run_visible_operation(
                "dump_publication",
                "Публикация snapshot… Подождите.",
                lambda: SnapshotPublicationService(FilesystemDumpRepository()).publish(
                    dumps_root=config.dumps_root,
                    snapshot=st.session_state.snapshot,
                    published_at=datetime.now(UTC),
                    media_resolver=FilesystemMediaResolver(config.working_root),
                ),
            )
            dump_path = config.dumps_root / f"dump-{dump_manifest.dump_number:06d}"
            bind_snapshot(
                st.session_state, st.session_state.snapshot, dump_manifest, dump_path
            )
            st.session_state.active_db = None
            st.toast("Current snapshot published as a new immutable dump.")
            st.rerun()
        except (ValueError, OSError, RuntimeError):
            _record_error(
                "Snapshot publication failed validation or filesystem checks."
            )
    st.divider()
    st.subheader("Repair manually edited dump")
    if _action_button("Check edited dump", disabled=blocked or selected is None):
        try:
            assert selected is not None
            plan = _run_visible_operation(
                "dump_restamp_plan",
                "Проверка дампа… Подождите.",
                lambda: plan_dump_restamp(selected),
            )
            st.session_state.restamp_plan = plan
        except (ValueError, OSError, RuntimeError):
            _record_error("Dump repair check failed.")
    repair_plan = st.session_state.get("restamp_plan")
    if repair_plan is not None:
        if repair_plan.aborted:
            st.error("Dump cannot be auto-repaired:")
            for problem in repair_plan.problems:
                st.write(f"- {problem}")
            st.session_state.restamp_plan = None
        elif not repair_plan.report_lines:
            st.info("Dump is already consistent; no repairs needed.")
            st.session_state.restamp_plan = None
        else:
            st.write("Planned repairs:")
            for line in repair_plan.report_lines:
                st.write(f"- {line}")
            rows_will_be_deleted = any(
                "removed" in line or "dropped" in line
                for line in repair_plan.report_lines
            )
            confirmation_label = (
                "Apply these repairs (rows will be deleted)"
                if rows_will_be_deleted
                else "Apply manifest re-sign"
            )
            confirm_repair = st.checkbox(confirmation_label, value=False)
            if _action_button("Apply dump re-sign", disabled=not confirm_repair):
                try:
                    report = _run_visible_operation(
                        "dump_restamp_apply",
                        "Применение правок и пере-подпись… Подождите.",
                        lambda: apply_dump_restamp(repair_plan),
                    )
                    st.session_state.restamp_plan = None
                    st.success(
                        f"Dump repaired and verified. run_id {report.run_id[:12]}"
                    )
                except (ValueError, OSError, RuntimeError):
                    _record_error("Dump re-sign failed.")
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
    selected_database = (
        st.selectbox(
            "Available verified DB",
            artifacts,
            format_func=lambda item: (
                item[0].name
                + (" (current)" if not database_is_stale(item[1], current_dump) else "")
            ),
        )
        if artifacts
        else None
    )
    if _action_button(
        "Load selected DB with its source dump",
        disabled=blocked or selected_database is None or config is None,
    ):
        try:
            assert config is not None and selected_database is not None
            database_path, database_manifest = selected_database
            source_dump = config.dumps_root / (
                f"dump-{database_manifest.source_dump_number:06d}"
            )
            dump_manifest, snapshot = _run_visible_operation(
                "database_loading",
                "Загрузка LadybugDB и связанного snapshot… Подождите.",
                lambda: load_verified_dump(source_dump),
            )
            if database_is_stale(database_manifest, dump_manifest):
                raise ValueError("database manifest does not match its source dump")
            bind_snapshot(st.session_state, snapshot, dump_manifest, source_dump)
            st.session_state.active_db = (database_path, database_manifest)
            st.toast("Database and its exact source snapshot loaded and selected.")
            st.rerun()
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error("Database or its exact source snapshot could not be loaded.")
    name = st.text_input("Safe new database name")
    base, emodel, eversion, dimension = _model_fields("db_")
    del base
    vmodel, vversion = (
        st.text_input("DB vision model", _environment_text("MVP_V2_VISION_MODEL")),
        st.text_input(
            "DB vision model version",
            _environment_text("MVP_V2_VISION_MODEL_VERSION"),
        ),
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
    if _action_button("Save as new DB", disabled=not build_ready):
        try:
            assert config is not None and selected is not None
            configure_ladybug_home(config)
            target = config.databases_root / f"{name}.lbdb"
            db_manifest = _run_visible_operation(
                "database_build",
                "Перестроение LadybugDB… Подождите.",
                lambda: build_database(
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
                ),
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
    base_url: str,
) -> tuple[Any, ...]:
    path, manifest = active
    configure_ladybug_home(config)
    ladybug = importlib.import_module("ladybug")
    database = connection = None
    try:
        database = ladybug.Database(str(path / "database.lbug"))
        connection = ladybug.Connection(database)
        service = SearchService(
            OllamaAdapter(LocalHttpTransport(base_url)),
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
        "Search Ollama base URL",
        _environment_text("MVP_V2_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        key=f"{prefix}_base",
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
    if _action_button(
        "Search evidence",
        disabled=config is None or active is None or not question.strip() or not allow,
    ):
        try:
            assert config is not None and active is not None
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
            st.session_state.search_evidence = _run_visible_operation(
                "search",
                "Поиск по базе… Подождите.",
                lambda: _with_search(config, active, request, base),
            )
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error(
                "Search failed safely; verify DB, model identity, and authorization."
            )
    for number, item in enumerate(st.session_state.get("search_evidence", ()), 1):
        st.write(f"Evidence {number}")
        st.write(item.text)
        chunk_label = (
            "asset"
            if item.matched_chunk_position is None
            else f"{item.matched_chunk_position + 1}/{item.document_chunk_count}"
        )
        st.write(
            f"[{number}] {item.document_title} — score={item.score:.6f}, "
            f"chunk={chunk_label}, context={item.context_scope}, "
            f"document={item.document_id}, version={item.version}, "
            f"embedding={item.embedding_id}, "
            f"source={item.source_url or item.local_path}"
        )


def _chat(config: UiRuntimeConfig | None) -> None:
    st.subheader("Chatbot")
    if _action_button("Clear chat history"):
        st.session_state.chat_history = []
    active = _active(config)
    base, limit, allow_embed = _search_fields("chat")
    limit = min(limit, MAX_EVIDENCE_ITEMS)
    model, version = (
        st.text_input("Exact chat model", _environment_text("MVP_V2_CHAT_MODEL")),
        st.text_input(
            "Exact chat model version", _environment_text("MVP_V2_CHAT_MODEL_VERSION")
        ),
    )
    if st.session_state.chat_history:
        st.download_button(
            "Export chat as Markdown",
            data=render_chat_markdown(st.session_state.chat_history),
            file_name=datetime.now().strftime("chat-export-%Y%m%d-%H%M%S.md"),
            mime="text/markdown",
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
                    answer = _run_visible_operation(
                        "chat",
                        "Поиск источников и генерация ответа… Подождите.",
                        lambda: service.answer(
                            ChatRequest(
                                question=question,
                                search=search_request,
                                model=model.strip(),
                                model_version=version.strip(),
                                authorization=ModelAuthorization(
                                    allow=True, operation="generation"
                                ),
                            )
                        ),
                    )
                finally:
                    if connection is not None:
                        connection.close()
                    if database is not None:
                        database.close()
                st.session_state.chat_history.append(
                    (
                        question,
                        answer,
                        ChatTurnProvenance.capture(
                            manifest=active[1],
                            chat_model=model.strip(),
                            chat_model_version=version.strip(),
                        ),
                    )
                )
            except (ValueError, OSError, RuntimeError, ValidationError):
                _record_error("Chat failed safely; no unverified answer was added.")
    for prior_question, answer, provenance in st.session_state.chat_history:
        st.write(f"Question: {prior_question}")
        dump_label = (
            provenance.source_dump_number
            if provenance.source_dump_number is not None
            else "?"
        )
        st.caption(
            f"model={provenance.chat_model or '?'}"
            f" v{provenance.chat_model_version or '?'}"
            f" · db={provenance.database_name or '?'}"
            f" · dump={dump_label}"
        )
        st.write(answer.answer)
        for citation in answer.citations:
            chunk_label = (
                "asset"
                if citation.matched_chunk_position is None
                else (
                    f"{citation.matched_chunk_position + 1}/"
                    f"{citation.document_chunk_count}"
                )
            )
            st.write(
                f"[{citation.number}] {citation.document_title} — "
                f"score={citation.score:.6f}, chunk={chunk_label}, "
                f"context={citation.context_scope}, "
                f"document={citation.document_id}, version={citation.version}, "
                f"evidence={citation.evidence_id}, "
                f"source={citation.source_url or citation.local_path}"
            )


def _errors(config: UiRuntimeConfig | None) -> None:
    st.subheader("Errors/Import report")
    blocked = _blocked(config)
    st.write(st.session_state.errors or "No operation errors.")
    st.caption(f"Application log: {application_log_path()}")
    try:
        log_tail = read_log_tail()
    except OSError:
        log_tail = "Log file cannot be read."
    if log_tail:
        st.text_area("Recent application log", log_tail, height=240, disabled=True)
        st.download_button(
            "Download current application log",
            data=log_tail.encode("utf-8"),
            file_name="mvp-v2-app.log",
            mime="text/plain",
        )
    available = (
        config is not None
        and config.working_root.is_dir()
        and not config.working_root.is_symlink()
    )
    if _action_button("Preview legacy import", disabled=blocked or not available):
        try:
            assert config is not None
            service = LegacyMigrationService(
                importer=CsvLegacyImporter(), dump_repository=FilesystemDumpRepository()
            )
            st.session_state.import_result = _run_visible_operation(
                "legacy_import_preview",
                "Проверка legacy-данных… Подождите.",
                lambda: service.preview(runtime_data_root=config.working_root),
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
    if _action_button(
        "Approve and publish legacy import",
        disabled=blocked or result is None or not approve or not approved.strip(),
    ):
        try:
            assert config is not None
            service = LegacyMigrationService(
                importer=CsvLegacyImporter(), dump_repository=FilesystemDumpRepository()
            )
            typed = cast(LegacyImportResult, result)
            manifest = _run_visible_operation(
                "legacy_import_publication",
                "Импорт legacy-данных… Подождите.",
                lambda: service.publish(
                    result=typed,
                    approved_report_id=approved,
                    dumps_root=config.dumps_root,
                    media_resolver=FilesystemMediaResolver(config.working_root),
                    published_at=datetime.now(UTC),
                ),
            )
            dump_path = config.dumps_root / f"dump-{manifest.dump_number:06d}"
            bind_snapshot(st.session_state, typed.snapshot, manifest, dump_path)
            st.session_state.active_db = None
            st.success("Approved legacy snapshot published and loaded into session.")
        except (ValueError, OSError, RuntimeError):
            _record_error(
                "Legacy approval or publication failed. Check exact report ID and report facts."  # noqa: E501
            )
