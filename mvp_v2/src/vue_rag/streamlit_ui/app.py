from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import sys
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, TypeVar, cast

import streamlit as st
from pydantic import ValidationError

from vue_rag.adapters.filesystem.dumps import FilesystemDumpRepository
from vue_rag.adapters.filesystem.media import FilesystemMediaResolver
from vue_rag.adapters.ladybug import LadybugAnn, build_database, load_verified_dump
from vue_rag.adapters.ollama import LocalHttpTransport, OllamaAdapter
from vue_rag.application.dto.chat import ChatRequest
from vue_rag.application.dto.deletion import (
    DeletionCommand,
    DeletionImpact,
    GraphNodeRef,
)
from vue_rag.application.dto.retrieval import (
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    SearchRequest,
)
from vue_rag.application.dump_lifecycle.dump_restamp import (
    apply_dump_restamp,
    plan_dump_restamp,
)
from vue_rag.application.dump_lifecycle.manifests import DatabaseManifest, DumpManifest
from vue_rag.application.dump_lifecycle.publication import SnapshotPublicationService
from vue_rag.application.graph_snapshot_operations.deletion import preview_deletion
from vue_rag.application.graph_snapshot_operations.document_editing import (
    DocumentMetadataUpdate,
    update_document_metadata,
)
from vue_rag.application.graph_snapshot_operations.document_view import (
    DocumentSummary,
    build_document_summaries,
    build_document_view,
)
from vue_rag.application.graph_snapshot_operations.reference_data import (
    initialize_reference_data,
)
from vue_rag.application.graph_snapshot_operations.relations import (
    RelationKind,
    RelationService,
)
from vue_rag.application.graph_snapshot_operations.title_backfill import (
    fill_missing_document_titles,
)
from vue_rag.application.ingestion_contracts import (
    EmbeddingRequest,
    IngestionRequest,
    ModelAuthorization,
    ModelIdentity,
    SourceKind,
)
from vue_rag.application.rag.agent.answer_rendering import render_citation_details
from vue_rag.application.rag.agent.chat import MAX_EVIDENCE_ITEMS, ChatService
from vue_rag.application.rag.agent.chat_export import (
    ChatTurnProvenance,
    render_chat_markdown,
)
from vue_rag.application.rag.preparation.chunk_backfill import (
    ChunkMutationImpact,
    build_missing_chunks,
    chunk_mutation_impact,
    delete_all_chunks_and_embeddings,
    regenerate_all_chunks,
)
from vue_rag.application.rag.preparation.model_operations import (
    build_embeddings,
    describe_asset,
)
from vue_rag.application.rag.retrieval.retrieval import SearchService
from vue_rag.bootstrap.ui import UiRuntimeConfig, default_runtime_config
from vue_rag.domain.graph import Asset, Document
from vue_rag.domain.schema_registry import NODE_SPECS
from vue_rag.domain.snapshot import GraphSnapshot
from vue_rag.streamlit_ui.controller import (
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
from vue_rag.streamlit_ui.logging import (
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
    "Errors",
)
_SAFE_DB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ResultT = TypeVar("_ResultT")


def _environment_text(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _ollama_adapter(base_url: str) -> OllamaAdapter:
    allowed_lan_hosts = frozenset(
        item.strip()
        for item in os.environ.get("MVP_V2_OLLAMA_ALLOWED_LAN_HOSTS", "").split(",")
        if item.strip()
    )
    return OllamaAdapter(
        LocalHttpTransport(base_url, allowed_lan_hosts=allowed_lan_hosts)
    )


def _environment_dimension() -> int:
    raw = os.environ.get("MVP_V2_EMBEDDING_DIMENSION", "768")
    try:
        value = int(raw)
    except ValueError:
        return 768
    return value if value > 0 else 768


def _environment_similarity_score() -> float:
    raw = os.environ.get("MVP_V2_MINIMUM_SIMILARITY_SCORE", "0.55")
    try:
        value = float(raw)
    except ValueError:
        return 0.55
    return value if math.isfinite(value) and -1.0 <= value <= 1.0 else 0.55


def _asset_label(asset: Asset) -> str:
    return asset.description.strip() or asset.local_path


def _custom_property_names(table_name: str) -> tuple[str, ...]:
    spec = next(item for item in NODE_SPECS if item.table_name == table_name)
    return tuple(item.name for item in spec.properties if item.custom)


def _node_record(table_name: str, node: Any) -> dict[str, Any]:
    record = cast(dict[str, Any], node.model_dump(exclude={"custom_properties"}))
    values = {item.name: item.value for item in node.custom_properties}
    spec = next(item for item in NODE_SPECS if item.table_name == table_name)
    for field in spec.properties:
        if field.custom:
            record[field.name] = values.get(field.name, field.default)
    return record


def _action_button(
    label: str,
    *,
    disabled: bool = False,
    primary: bool = False,
    key: str | None = None,
) -> bool:
    clicked = st.button(
        label,
        disabled=disabled,
        type="primary" if primary else "secondary",
        key=key,
    )
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
    st.session_state.setdefault("snapshot_revision", 0)
    st.session_state.setdefault("errors", [])
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("active_db", None)
    st.session_state.setdefault("snapshot_dump_run_id", None)
    st.session_state.setdefault("snapshot_dirty", True)
    st.session_state.setdefault("restamp_plan", None)
    st.session_state.setdefault("chunk_regeneration_preview", None)
    st.session_state.setdefault("chunk_deletion_preview", None)
    st.session_state.setdefault("chunk_preview_sequence", 0)


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
    runtime_ready = config is not None and all(
        path.is_dir() and not path.is_symlink()
        for path in (
            config.working_root,
            config.dumps_root,
            config.databases_root,
            config.ladybug_home,
        )
    )
    publish_clicked = st.sidebar.button(
        "Publish changes as new dump",
        type="primary",
        disabled=not snapshot_dirty or not runtime_ready,
    )
    if publish_clicked:
        record_user_action("Publish changes as new dump")
        assert config is not None
        _publish_session_snapshot(config)
    elif not snapshot_dirty:
        st.sidebar.caption("No unpublished changes.")


def _publish_session_snapshot(config: UiRuntimeConfig) -> None:
    try:
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
        _record_error("Snapshot publication failed validation or filesystem checks.")


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


def _clear_upload_state(state: MutableMapping[str, Any], key_prefix: str) -> None:
    prefix = f"{key_prefix}_"
    for key in tuple(state):
        if key.startswith(prefix):
            del state[key]


def _upload_input_fingerprint(
    *,
    kind: str,
    title: str,
    source_type_id: str | None,
    external_id: str,
    status_id: str | None,
    source_url: str,
    process_id: str,
    role_id: str,
    document_id: str | None,
    snapshot_revision: int,
    uploads: tuple[Any, ...],
) -> str:
    payload = {
        "kind": kind,
        "title": title,
        "source_type_id": source_type_id,
        "external_id": external_id,
        "status_id": status_id,
        "source_url": source_url,
        "process_id": process_id,
        "role_id": role_id,
        "document_id": document_id,
        "snapshot_revision": snapshot_revision,
        "uploads": [
            {
                "name": str(item.name),
                "sha256": hashlib.sha256(item.getvalue()).hexdigest(),
            }
            for item in uploads
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _invalidate_upload_preview(
    state: MutableMapping[str, Any],
    *,
    preview_key: str,
    fingerprint_key: str,
) -> None:
    state[preview_key] = None
    state[fingerprint_key] = None


def _current_upload_preview(
    state: MutableMapping[str, Any],
    *,
    preview_key: str,
    fingerprint_key: str,
    fingerprint: str,
) -> Any | None:
    if state.get(fingerprint_key) != fingerprint:
        return None
    return state.get(preview_key)


def _upload(config: UiRuntimeConfig | None) -> None:
    st.subheader("Upload")
    snapshot = st.session_state.snapshot
    missing_reference_data = tuple(
        label
        for label, values in (
            ("SourceType", snapshot.source_types),
            ("Status", snapshot.statuses),
        )
        if not values
    )
    if missing_reference_data:
        st.warning(
            "Workspace needs current reference data: "
            + ", ".join(missing_reference_data)
            + "."
        )
        if _action_button(
            "Initialize workspace",
            disabled=_blocked(config),
            key="initialize_current_workspace",
        ):
            try:
                updated = _run_visible_operation(
                    "initialize_workspace",
                    "Инициализация current workspace…",
                    lambda: initialize_reference_data(snapshot),
                )
                mark_snapshot_dirty(st.session_state, updated)
                st.success(
                    "Current SourceType and Status initialized in the session snapshot."
                )
            except (ValueError, ValidationError):
                _record_error(
                    "Workspace initialization failed; the snapshot was not changed."
                )
    _render_upload(config, key_prefix="upload")


def _linked_target(relations: tuple[Any, ...], source_id: str) -> str | None:
    return next(
        (item.target_id for item in relations if item.source_id == source_id),
        None,
    )


def _select_index(options: tuple[str, ...], preferred: str | None) -> int:
    return options.index(preferred) if preferred in options else 0


def _render_upload(
    config: UiRuntimeConfig | None,
    *,
    key_prefix: str,
    document: Document | None = None,
) -> None:
    blocked = _blocked(config)
    missing = False if config is None else _missing(config)
    snapshot = st.session_state.snapshot
    kind_options = ("url", "pdf", "markdown", "html", "image_collection")
    preferred_kind = None if document is None else document.content_format
    kind = st.selectbox(
        "Source kind",
        kind_options,
        index=_select_index(kind_options, preferred_kind),
        key=f"{key_prefix}_kind",
    )
    source_types = {item.id: item.code for item in snapshot.source_types}
    statuses = {item.id: item.name for item in snapshot.statuses}
    processes = {item.id: item.name for item in snapshot.processes}
    roles = {item.id: item.name for item in snapshot.roles}
    version_id = None
    if document is not None:
        version_ids = {
            item.target_id
            for item in snapshot.has_versions
            if item.source_id == document.id
        }
        version_id = next(
            (
                item.id
                for item in snapshot.document_versions
                if item.id in version_ids and item.is_current
            ),
            None,
        )
    preferred_source_type = (
        None
        if document is None
        else _linked_target(snapshot.has_source_types, document.id)
    )
    source_type_options = tuple(source_types)
    source_type_id = (
        st.selectbox(
            "SourceType",
            source_type_options,
            index=_select_index(source_type_options, preferred_source_type),
            format_func=lambda value: source_types[value],
            key=f"{key_prefix}_source_type",
        )
        if source_types
        else None
    )
    preferred_status = (
        None
        if version_id is None
        else _linked_target(snapshot.has_statuses, version_id)
    )
    status_options = tuple(statuses)
    status_id = (
        st.selectbox(
            "Required Status",
            status_options,
            index=_select_index(status_options, preferred_status),
            format_func=lambda value: statuses[value],
            key=f"{key_prefix}_status",
        )
        if statuses
        else None
    )
    process_options = ("", *processes)
    preferred_process = (
        None if document is None else _linked_target(snapshot.describes, document.id)
    )
    process_id = st.selectbox(
        "Process (optional)",
        process_options,
        index=(
            _select_index(process_options, preferred_process)
            if document is not None
            else _preferred_option_index(processes, "onboarding")
        ),
        format_func=lambda value: processes.get(value, "Not selected"),
        key=f"{key_prefix}_process",
    )
    role_options = ("", *roles)
    preferred_role = (
        None if document is None else _linked_target(snapshot.for_roles, document.id)
    )
    role_id = st.selectbox(
        "Role (optional)",
        role_options,
        index=(
            _select_index(role_options, preferred_role)
            if document is not None
            else _preferred_option_index(roles, "student")
        ),
        format_func=lambda value: roles.get(value, "Not selected"),
        key=f"{key_prefix}_role",
    )
    title = st.text_input(
        "Title",
        value="" if document is None else document.title,
        help=(
            "Optional for Markdown: the first H1 is used, then the file name. "
            "A typed title overrides both."
        ),
        key=f"{key_prefix}_title",
    )
    external_id_key = f"{key_prefix}_external_id"
    if document is not None:
        st.session_state[external_id_key] = document.external_id
        external_id = st.text_input(
            "Existing/new identity (external_id)",
            disabled=True,
            key=external_id_key,
        )
    else:
        external_id = st.text_input(
            "Existing/new identity (external_id)",
            value="",
            key=external_id_key,
        )
    if document is not None:
        st.caption(f"New version of Document `{document.id}`.")
    st.caption(
        "Optional for PDF, Markdown, and HTML: without a Source URL, identity is "
        "generated as local:<primary file path>. Image collections require an "
        "external_id or Source URL."
    )
    source_url_label = (
        "Public HTTP(S) URL (required)"
        if kind == "url"
        else "Source URL (optional provenance)"
    )
    source_url = st.text_input(
        source_url_label,
        value="" if document is None else document.source_url,
        key=f"{key_prefix}_source_url",
    )
    uploads = (
        st.file_uploader(
            "Local input",
            accept_multiple_files=True,
            key=f"{key_prefix}_files",
        )
        if kind != "url"
        else None
    )
    upload_values = tuple(uploads or ())
    fingerprint = _upload_input_fingerprint(
        kind=kind,
        title=title,
        source_type_id=source_type_id,
        external_id=external_id,
        status_id=status_id,
        source_url=source_url,
        process_id=process_id,
        role_id=role_id,
        document_id=None if document is None else document.id,
        snapshot_revision=int(st.session_state.get("snapshot_revision", 0)),
        uploads=upload_values,
    )
    title_is_valid = bool(title.strip()) or kind == "markdown"
    identity_is_valid = (
        document is not None
        or kind != "image_collection"
        or bool(external_id.strip() or source_url.strip())
    )
    valid = bool(
        title_is_valid
        and identity_is_valid
        and source_type_id
        and status_id
        and (source_url.strip() if kind == "url" else uploads)
    )
    preview_key = f"{key_prefix}_preview"
    fingerprint_key = f"{key_prefix}_preview_fingerprint"
    if _action_button(
        "Preview",
        disabled=blocked or missing or not valid,
        key=f"{key_prefix}_preview_action",
    ):
        _invalidate_upload_preview(
            cast(MutableMapping[str, Any], st.session_state),
            preview_key=preview_key,
            fingerprint_key=fingerprint_key,
        )
        try:
            assert config is not None
            input_root, input_path = config.working_root, ""
            if kind != "url":
                input_path = primary_upload_name(cast(SourceKind, kind), upload_values)
                input_root = stage_uploads(config.working_root, upload_values)
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
                document_id=None if document is None else document.id,
            )
            try:
                generated_preview = _run_visible_operation(
                    "ingestion_preview",
                    "Подготовка документа… Подождите.",
                    lambda: preview_ingestion(
                        snapshot,
                        request,
                        working_root=config.working_root,
                        input_root=input_root,
                    ),
                )
                st.session_state[preview_key] = generated_preview
                st.session_state[fingerprint_key] = fingerprint
            finally:
                if kind != "url":
                    remove_staging_directory(input_root, config.working_root)
        except (ValueError, OSError, RuntimeError, ValidationError):
            _record_error("Ingestion preview failed. Check inputs and runtime paths.")
    stored_preview = st.session_state.get(preview_key)
    preview = _current_upload_preview(
        cast(MutableMapping[str, Any], st.session_state),
        preview_key=preview_key,
        fingerprint_key=fingerprint_key,
        fingerprint=fingerprint,
    )
    if stored_preview is not None and preview is None:
        st.warning("Inputs changed. Run Preview again before Commit.")
    if preview is not None:
        st.json(
            {
                "document_id": preview.document_id,
                "title": preview.request.title,
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
    if _action_button(
        "Commit",
        disabled=blocked or missing or preview is None,
        key=f"{key_prefix}_commit_action",
    ):
        try:
            assert config is not None
            assert preview is not None
            committed = _run_visible_operation(
                "ingestion_commit",
                "Загрузка документа… Подождите.",
                lambda: commit_ingestion(
                    snapshot, preview, working_root=config.working_root
                ),
            )
            mark_snapshot_dirty(st.session_state, committed)
            st.session_state[preview_key] = None
            st.session_state[fingerprint_key] = None
            if document is not None:
                st.session_state.document_notice = (
                    "success",
                    f"Version {preview.version} was committed for {document.id}.",
                )
                st.rerun()
            st.success(
                "Committed to the session snapshot and working root. No dump or DB was published."  # noqa: E501
            )
        except (ValueError, OSError, RuntimeError):
            _record_error(
                "Ingestion commit failed. No session snapshot change was applied."
            )


@st.dialog("Upload new version", width="large")
def _version_upload_dialog(config: UiRuntimeConfig | None, document_id: str) -> None:
    document = next(
        (
            item
            for item in st.session_state.snapshot.documents
            if item.id == document_id
        ),
        None,
    )
    if document is None:
        st.error("The selected document is no longer available.")
        return
    _render_upload(
        config,
        key_prefix=f"version_{document_id}",
        document=document,
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
                st.json([_node_record("Asset", asset) for asset in view.assets])
    except (TypeError, ValueError):
        _record_error("Selected document could not be previewed.")


def _render_document_actions(
    *,
    config: UiRuntimeConfig | None,
    blocked: bool,
    snapshot: GraphSnapshot,
    summary: DocumentSummary,
) -> None:
    del snapshot
    document = summary.document
    if _action_button(
        "Upload new version",
        disabled=blocked,
        key=f"upload_new_version_{document.id}",
    ):
        key_prefix = f"version_{document.id}"
        _clear_upload_state(
            cast(MutableMapping[str, Any], st.session_state), key_prefix
        )
        _version_upload_dialog(config, document.id)
    if document.source_url.startswith(("http://", "https://")):
        st.link_button("Open source URL", document.source_url)
    elif document.source_url:
        st.caption(f"Source: {document.source_url}")


def _editor_records(value: Any) -> list[dict[str, Any]]:
    records = (
        value if isinstance(value, list) else cast(Any, value).to_dict(orient="records")
    )
    return cast(list[dict[str, Any]], records)


def _metadata_updates_from_editor(value: Any) -> tuple[DocumentMetadataUpdate, ...]:
    records = _editor_records(value)
    updates: list[DocumentMetadataUpdate] = []
    for record in records:
        is_active = record.get("is_active")
        if type(is_active) is not bool:
            raise ValueError("is_active must be boolean")
        updates.append(
            DocumentMetadataUpdate(
                document_id=str(record.get("id", "")),
                title=str(record.get("title", "")),
                source_url=str(record.get("source_url", "")),
                external_id=str(record.get("external_id", "")),
                summary=str(record.get("summary", "")),
                is_active=is_active,
            )
        )
    return tuple(updates)


def _document_metadata_diff(
    snapshot: GraphSnapshot, updates: tuple[DocumentMetadataUpdate, ...]
) -> list[dict[str, object]]:
    original = {item.id: item for item in snapshot.documents}
    changes: list[dict[str, object]] = []
    for update in updates:
        document = original.get(update.document_id)
        if document is None:
            continue
        proposed = {
            "title": " ".join(update.title.split()),
            "source_url": update.source_url.strip(),
            "external_id": update.external_id.strip(),
            "summary": update.summary,
            "is_active": update.is_active,
        }
        for field, after in proposed.items():
            before = getattr(document, field)
            if before != after:
                changes.append(
                    {
                        "document_id": document.id,
                        "field": field,
                        "before": before,
                        "after": after,
                    }
                )
    return changes


def _render_document_metadata_commit(
    *, blocked: bool, snapshot: GraphSnapshot, edited: Any
) -> None:
    try:
        updates = _metadata_updates_from_editor(edited)
        changes = _document_metadata_diff(snapshot, updates)
        if changes:
            st.markdown("**Pending changes**")
            st.dataframe(changes, width="stretch", hide_index=True)
        else:
            st.info("No metadata changes to commit.")
        if _action_button(
            "Commit changes",
            disabled=blocked or not changes,
            primary=True,
        ):
            updated = update_document_metadata(snapshot, updates)
            mark_snapshot_dirty(st.session_state, updated)
            revision = int(st.session_state.get("document_editor_revision", 0))
            st.session_state.document_editor_revision = revision + 1
            st.toast("Metadata changes committed to the session snapshot.")
            st.rerun()
    except (TypeError, ValueError, ValidationError):
        _record_error("Document metadata is invalid. Fix the values before commit.")


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
    st.markdown("**Deletion confirmation**")
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

    revision = int(st.session_state.get("document_editor_revision", 0))
    edited = st.data_editor(
        [
            {
                "selected": False,
                "id": item.document.id,
                "title": item.document.title,
                "source_url": item.document.source_url,
                "external_id": item.document.external_id,
                "summary": item.document.summary,
                **{
                    name: _node_record("Document", item.document)[name]
                    for name in _custom_property_names("Document")
                },
                "format": item.document.content_format,
                "local_path": "" if item.version is None else item.version.local_path,
                "version": None if item.version is None else item.version.version,
                "chunks": item.chunk_count,
                "assets": item.asset_count,
                "count_links_out": item.count_links_out,
                "count_links_in": item.count_links_in,
                "is_active": item.document.is_active,
            }
            for item in summaries
        ],
        key=f"documents_table_editor_{revision}",
        disabled=(
            "id",
            "format",
            "local_path",
            "version",
            "chunks",
            "assets",
            "count_links_out",
            "count_links_in",
            *_custom_property_names("Document"),
        ),
        column_config={
            "selected": st.column_config.CheckboxColumn(
                "Select", help="Select one document for preview or deletion."
            )
        },
        num_rows="fixed",
        width="stretch",
    )
    _render_document_metadata_commit(blocked=blocked, snapshot=snapshot, edited=edited)

    selected_ids = [
        str(record.get("id", ""))
        for record in _editor_records(edited)
        if record.get("selected") is True
    ]
    summary_by_id = {item.document.id: item for item in summaries}
    selected_summary = (
        summary_by_id.get(selected_ids[0]) if len(selected_ids) == 1 else None
    )
    if len(selected_ids) > 1:
        st.warning("Select only one document for preview or deletion.")
    elif selected_summary is None:
        st.info("Select one document to preview or delete it.")
    else:
        _render_document_preview(snapshot, selected_summary)
        _render_document_actions(
            config=config,
            blocked=blocked,
            snapshot=snapshot,
            summary=selected_summary,
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


def _chunk_impact_record(impact: ChunkMutationImpact) -> dict[str, int]:
    return {
        "chunks before": impact.chunks_before,
        "chunks after": impact.chunks_after,
        "chunks removed": impact.chunks_removed,
        "chunks added": impact.chunks_added,
        "chunk embeddings removed": impact.chunk_embeddings_removed,
        "chunk-to-asset edges removed": impact.contains_asset_edges_removed,
    }


def _set_chunk_preview(
    state_key: str,
    action: str,
    before: GraphSnapshot,
    after: GraphSnapshot,
) -> None:
    sequence = int(st.session_state.chunk_preview_sequence) + 1
    st.session_state.chunk_preview_sequence = sequence
    st.session_state[state_key] = {
        "action": action,
        "revision": int(st.session_state.snapshot_revision),
        "candidate": after,
        "impact": chunk_mutation_impact(before, after),
        "confirmation_key": f"{state_key}_confirm_{sequence}",
    }


def _render_chunk_preview(state_key: str, commit_label: str) -> GraphSnapshot | None:
    preview = st.session_state.get(state_key)
    if not isinstance(preview, dict):
        return None
    if preview.get("revision") != int(st.session_state.snapshot_revision):
        st.session_state[state_key] = None
        st.warning("Chunk preview is stale. Generate a new preview.")
        return None
    candidate = preview.get("candidate")
    impact = preview.get("impact")
    confirmation_key = preview.get("confirmation_key")
    if (
        not isinstance(candidate, GraphSnapshot)
        or not isinstance(impact, ChunkMutationImpact)
        or not isinstance(confirmation_key, str)
    ):
        st.session_state[state_key] = None
        return None
    st.write(_chunk_impact_record(impact))
    confirmed = st.checkbox(
        "I understand that existing chunks and their embeddings will be removed.",
        value=False,
        key=confirmation_key,
    )
    if _action_button(commit_label, disabled=not confirmed, primary=True):
        mark_snapshot_dirty(st.session_state, candidate)
        st.session_state.chunk_regeneration_preview = None
        st.session_state.chunk_deletion_preview = None
        st.success(f"{preview['action']} applied to the session snapshot.")
        return candidate
    return None


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
    st.divider()
    st.subheader("Destructive chunk maintenance")
    st.warning(
        "These actions change only the session snapshot. Publish a new dump and DB "
        "only after reviewing the impact. Asset embeddings are preserved."
    )
    regenerate_col, delete_col = st.columns(2)
    with regenerate_col:
        if _action_button(
            "Preview regenerate all chunks",
            disabled=blocked or not snapshot.chunks,
        ):
            try:
                assert config is not None
                candidate = _run_visible_operation(
                    "chunk_regeneration_preview",
                    "Проверка файлов и регенерация preview… Подождите.",
                    lambda: regenerate_all_chunks(
                        snapshot, FilesystemMediaResolver(config.working_root)
                    ),
                )
                _set_chunk_preview(
                    "chunk_regeneration_preview",
                    "Chunk regeneration",
                    snapshot,
                    candidate,
                )
                st.session_state.chunk_deletion_preview = None
            except (ValueError, OSError, RuntimeError, ValidationError):
                st.session_state.chunk_regeneration_preview = None
                _record_error(
                    "Chunk regeneration preview failed; the snapshot was not changed."
                )
    with delete_col:
        if _action_button(
            "Preview delete all chunks and chunk embeddings",
            disabled=blocked or not snapshot.chunks,
        ):
            try:
                candidate = delete_all_chunks_and_embeddings(snapshot)
                _set_chunk_preview(
                    "chunk_deletion_preview",
                    "Chunk deletion",
                    snapshot,
                    candidate,
                )
                st.session_state.chunk_regeneration_preview = None
            except (ValueError, RuntimeError, ValidationError):
                st.session_state.chunk_deletion_preview = None
                _record_error(
                    "Chunk deletion preview failed; the snapshot was not changed."
                )
    applied = _render_chunk_preview(
        "chunk_regeneration_preview", "Regenerate all chunks"
    ) or _render_chunk_preview(
        "chunk_deletion_preview", "Delete all chunks and chunk embeddings"
    )
    if applied is not None:
        snapshot = applied

    st.dataframe([_node_record("Chunk", item) for item in snapshot.chunks])
    st.dataframe([_node_record("Asset", item) for item in snapshot.assets])
    st.dataframe(
        [
            {
                **_node_record("Embedding", item),
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
                        _ollama_adapter(base),
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
                    vision=_ollama_adapter(base),
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
            f'/tmp/vue-graph-rag-mvp-v2/.venv/bin/python -m vue_rag.bootstrap.provision_vector --home "{config.ladybug_home}" --yes'  # noqa: E501
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
            _ollama_adapter(base_url),
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
                minimum_score=_environment_similarity_score(),
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
                    adapter = _ollama_adapter(base)
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
                        minimum_score=_environment_similarity_score(),
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
            st.write(render_citation_details(citation))


def _errors(config: UiRuntimeConfig | None) -> None:
    del config
    st.subheader("Errors")
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
