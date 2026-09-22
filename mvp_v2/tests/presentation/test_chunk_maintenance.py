from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest

import mvp_v2.presentation.app as app
from mvp_v2.application.rag.preparation.chunk_backfill import (
    delete_all_chunks_and_embeddings,
)
from mvp_v2.domain.snapshot import GraphSnapshot
from tests.factories import build_valid_snapshot


class SessionState(dict[str, Any]):
    def __getattr__(self, name: str) -> Any:
        return self[name]

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class StreamlitStub:
    def __init__(self, state: SessionState, *, confirmed: bool = False) -> None:
        self.session_state = state
        self.confirmed = confirmed
        self.warnings: list[str] = []
        self.successes: list[str] = []
        self.records: list[object] = []

    def write(self, value: object) -> None:
        self.records.append(value)

    def warning(self, value: str) -> None:
        self.warnings.append(value)

    def success(self, value: str) -> None:
        self.successes.append(value)

    def checkbox(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        return self.confirmed


def _state(snapshot: GraphSnapshot) -> SessionState:
    return SessionState(
        snapshot=snapshot,
        snapshot_revision=4,
        snapshot_dirty=False,
        snapshot_dump_run_id="run",
        active_db=("path", "manifest"),
        chunk_preview_sequence=0,
        chunk_regeneration_preview=None,
        chunk_deletion_preview=None,
    )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    state: SessionState,
    *,
    confirmed: bool,
    clicked: Callable[..., bool],
) -> StreamlitStub:
    streamlit = StreamlitStub(state, confirmed=confirmed)
    monkeypatch.setattr(app, "st", streamlit)
    monkeypatch.setattr(app, "_action_button", clicked)
    return streamlit


def test_chunk_preview_requires_confirmation_and_rejects_stale_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = build_valid_snapshot()
    candidate = delete_all_chunks_and_embeddings(before)
    state = _state(before)
    button_calls: list[tuple[str, dict[str, object]]] = []

    def disabled_click(label: str, **kwargs: object) -> bool:
        button_calls.append((label, kwargs))
        return False

    streamlit = _install(
        monkeypatch,
        state,
        confirmed=False,
        clicked=disabled_click,
    )
    app._set_chunk_preview(
        "chunk_deletion_preview", "Chunk deletion", before, candidate
    )

    applied = app._render_chunk_preview(
        "chunk_deletion_preview", "Delete all chunks and chunk embeddings"
    )

    assert applied is None
    assert state["snapshot"] == before
    assert state["snapshot_revision"] == 4
    assert state["snapshot_dirty"] is False
    assert streamlit.records
    assert button_calls == [
        ("Delete all chunks and chunk embeddings", {"disabled": True, "primary": True})
    ]

    stale_state = _state(before)
    stale_streamlit = _install(
        monkeypatch,
        stale_state,
        confirmed=True,
        clicked=lambda *args, **kwargs: pytest.fail("stale preview exposed commit"),
    )
    app._set_chunk_preview(
        "chunk_deletion_preview", "Chunk deletion", before, candidate
    )
    stale_state["snapshot_revision"] = 5

    assert (
        app._render_chunk_preview(
            "chunk_deletion_preview", "Delete all chunks and chunk embeddings"
        )
        is None
    )
    assert stale_state["chunk_deletion_preview"] is None
    assert stale_state["snapshot"] == before
    assert stale_streamlit.warnings == [
        "Chunk preview is stale. Generate a new preview."
    ]


def test_confirmed_chunk_preview_applies_only_candidate_and_invalidates_previews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = build_valid_snapshot()
    candidate = delete_all_chunks_and_embeddings(before)
    state = _state(before)
    button_calls: list[tuple[str, dict[str, object]]] = []

    def click(label: str, **kwargs: object) -> bool:
        button_calls.append((label, kwargs))
        return label == "Delete all chunks and chunk embeddings"

    streamlit = _install(
        monkeypatch,
        state,
        confirmed=True,
        clicked=click,
    )
    dump_repository = Mock(side_effect=AssertionError("dump write attempted"))
    database_builder = Mock(side_effect=AssertionError("database build attempted"))
    monkeypatch.setattr(app, "FilesystemDumpRepository", dump_repository)
    monkeypatch.setattr(app, "build_database", database_builder)
    app._set_chunk_preview(
        "chunk_deletion_preview", "Chunk deletion", before, candidate
    )
    state["chunk_regeneration_preview"] = {"other": "preview"}

    applied = app._render_chunk_preview(
        "chunk_deletion_preview", "Delete all chunks and chunk embeddings"
    )

    assert applied == candidate
    assert state["snapshot"] == candidate
    assert state["snapshot_revision"] == 5
    assert state["snapshot_dirty"] is True
    assert state["active_db"] is None
    assert state["chunk_regeneration_preview"] is None
    assert state["chunk_deletion_preview"] is None
    assert streamlit.successes
    assert button_calls == [
        ("Delete all chunks and chunk embeddings", {"disabled": False, "primary": True})
    ]
    dump_repository.assert_not_called()
    database_builder.assert_not_called()
