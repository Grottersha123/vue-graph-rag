from __future__ import annotations

from vue_rag.domain import graph
from vue_rag.domain.invariants import validate_graph
from vue_rag.domain.snapshot import GraphSnapshot

DEFAULT_SOURCE_TYPE = graph.SourceType(id="source-upload", code="upload")
DEFAULT_STATUS = graph.Status(
    id="status-ready",
    name="ready",
    description="Ready for processing",
)


def initialize_reference_data(snapshot: GraphSnapshot) -> GraphSnapshot:
    """Add only missing minimal current-schema upload reference data."""
    source_types = snapshot.source_types or (DEFAULT_SOURCE_TYPE,)
    statuses = snapshot.statuses or (DEFAULT_STATUS,)
    if source_types == snapshot.source_types and statuses == snapshot.statuses:
        return snapshot
    candidate = snapshot.model_copy(
        update={"source_types": source_types, "statuses": statuses}
    )
    validation = validate_graph(candidate)
    if not validation.is_valid:
        raise ValueError("initialized reference data violates graph invariants")
    return candidate
