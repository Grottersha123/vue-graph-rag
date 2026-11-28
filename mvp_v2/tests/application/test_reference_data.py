from __future__ import annotations

from vue_rag.application.graph_snapshot_operations.reference_data import (
    DEFAULT_SOURCE_TYPE,
    DEFAULT_STATUS,
    initialize_reference_data,
)
from vue_rag.domain import graph
from vue_rag.domain.invariants import validate_graph
from vue_rag.domain.snapshot import GraphSnapshot


def test_empty_snapshot_gets_minimal_current_reference_data() -> None:
    initialized = initialize_reference_data(GraphSnapshot())

    assert initialized.source_types == (DEFAULT_SOURCE_TYPE,)
    assert initialized.statuses == (DEFAULT_STATUS,)
    assert validate_graph(initialized).is_valid


def test_bootstrap_preserves_existing_reference_tables_and_adds_only_missing() -> None:
    existing = graph.SourceType(id="source-existing", code="existing")
    snapshot = GraphSnapshot(source_types=(existing,))

    initialized = initialize_reference_data(snapshot)

    assert initialized.source_types == (existing,)
    assert initialized.statuses == (DEFAULT_STATUS,)
    assert initialize_reference_data(initialized) is initialized
