from __future__ import annotations

import pytest

from tests.factories import build_valid_snapshot
from vue_rag.application.graph_snapshot_operations.document_editing import (
    DocumentMetadataUpdate,
    rename_document,
    update_document_metadata,
)
from vue_rag.application.graph_snapshot_operations.document_view import (
    build_document_summaries,
)
from vue_rag.domain.graph import LinksTo


def test_document_summary_combines_document_and_current_version() -> None:
    snapshot = build_valid_snapshot()

    summaries = build_document_summaries(snapshot)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.document.id == "document"
    assert summary.version is not None
    assert summary.version.id == "version"
    assert summary.chunk_count == 1
    assert summary.asset_count == 1
    assert summary.count_links_out == 0
    assert summary.count_links_in == 0


def test_document_summary_keeps_document_without_current_version() -> None:
    snapshot = build_valid_snapshot().model_copy(update={"has_versions": ()})

    summary = build_document_summaries(snapshot)[0]

    assert summary.document.id == "document"
    assert summary.version is None
    assert summary.chunk_count == 0
    assert summary.asset_count == 0
    assert summary.count_links_out == 0
    assert summary.count_links_in == 0


def test_document_summary_counts_incoming_and_outgoing_links() -> None:
    snapshot = build_valid_snapshot()
    other_document = snapshot.documents[0].model_copy(update={"id": "other"})
    snapshot = snapshot.model_copy(
        update={
            "documents": (*snapshot.documents, other_document),
            "links_to": (
                LinksTo.model_validate({"from": "document", "to": "other"}),
                LinksTo.model_validate({"from": "other", "to": "document"}),
                LinksTo.model_validate({"from": "document", "to": "document"}),
            ),
        }
    )

    summaries = {
        summary.document.id: summary for summary in build_document_summaries(snapshot)
    }

    assert summaries["document"].count_links_out == 2
    assert summaries["document"].count_links_in == 2
    assert summaries["other"].count_links_out == 1
    assert summaries["other"].count_links_in == 1


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


def test_update_document_metadata_changes_editable_fields() -> None:
    snapshot = build_valid_snapshot()

    updated = update_document_metadata(
        snapshot,
        (
            DocumentMetadataUpdate(
                document_id="document",
                title=" Updated title ",
                source_url=" https://example.test/source ",
                external_id=" guide-1 ",
                summary="",
                is_active=False,
            ),
        ),
    )

    assert snapshot.documents[0].title == "Title"
    assert updated.documents[0].model_dump() == {
        **snapshot.documents[0].model_dump(),
        "title": "Updated title",
        "source_url": "https://example.test/source",
        "external_id": "guide-1",
        "is_active": False,
    }


def test_update_document_metadata_requires_exact_document_set() -> None:
    with pytest.raises(ValueError, match="every document"):
        update_document_metadata(build_valid_snapshot(), ())


def test_update_document_metadata_rejects_duplicate_external_ids() -> None:
    snapshot = build_valid_snapshot()
    second = snapshot.documents[0].model_copy(update={"id": "other"})
    snapshot = snapshot.model_copy(update={"documents": (*snapshot.documents, second)})
    updates = tuple(
        DocumentMetadataUpdate(
            document_id=document.id,
            title=document.title,
            source_url=document.source_url,
            external_id="duplicate",
            summary="",
            is_active=document.is_active,
        )
        for document in snapshot.documents
    )

    with pytest.raises(ValueError, match="external_id"):
        update_document_metadata(snapshot, updates)
