from __future__ import annotations

import pytest

from mvp_v2.application.document_editing import rename_document
from mvp_v2.application.document_view import build_document_summaries
from tests.factories import build_valid_snapshot


def test_document_summary_combines_document_and_current_version() -> None:
    snapshot = build_valid_snapshot()

    summaries = build_document_summaries(snapshot)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.document.id == "document"
    assert summary.version.id == "version"
    assert summary.chunk_count == 1
    assert summary.asset_count == 1


def test_rename_document_normalizes_title_without_mutating_input() -> None:
    snapshot = build_valid_snapshot()

    updated = rename_document(snapshot, "document", "  New\n  title  ")

    assert snapshot.documents[0].title == "Title"
    assert updated.documents[0].title == "New title"


def test_rename_document_returns_same_snapshot_for_unchanged_title() -> None:
    snapshot = build_valid_snapshot()

    assert rename_document(snapshot, "document", "Title") is snapshot


@pytest.mark.parametrize("document_id,title", (("missing", "Title"), ("document", " ")))
def test_rename_document_rejects_invalid_request(document_id: str, title: str) -> None:
    with pytest.raises(ValueError):
        rename_document(build_valid_snapshot(), document_id, title)
