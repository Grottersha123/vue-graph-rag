from __future__ import annotations

from enum import StrEnum

from vue_rag.domain.graph import Describes, ForRole, LinksTo
from vue_rag.domain.invariants import validate_graph
from vue_rag.domain.snapshot import GraphSnapshot


class RelationKind(StrEnum):
    LINKS_TO = "LINKS_TO"
    DESCRIBES = "DESCRIBES"
    FOR_ROLE = "FOR_ROLE"


class RelationError(ValueError):
    pass


class RelationService:
    def add(
        self,
        snapshot: GraphSnapshot,
        kind: RelationKind,
        source_id: str,
        target_id: str,
    ) -> GraphSnapshot:
        self._check_endpoints(snapshot, kind, source_id, target_id)
        field, relation_type = _definition(kind)
        rows = getattr(snapshot, field)
        relation = relation_type.model_validate({"from": source_id, "to": target_id})
        if relation in rows:
            raise RelationError("relation already exists")
        candidate = snapshot.model_copy(update={field: (*rows, relation)})
        self._validate(candidate)
        return GraphSnapshot.model_validate(candidate.model_dump())

    def delete(
        self,
        snapshot: GraphSnapshot,
        kind: RelationKind,
        source_id: str,
        target_id: str,
    ) -> GraphSnapshot:
        field, _ = _definition(kind)
        rows = getattr(snapshot, field)
        remaining = tuple(
            row
            for row in rows
            if (row.source_id, row.target_id) != (source_id, target_id)
        )
        if len(remaining) == len(rows):
            raise RelationError("relation does not exist")
        candidate = snapshot.model_copy(update={field: remaining})
        self._validate(candidate)
        return GraphSnapshot.model_validate(candidate.model_dump())

    @staticmethod
    def _check_endpoints(
        snapshot: GraphSnapshot, kind: RelationKind, source_id: str, target_id: str
    ) -> None:
        documents = {item.id for item in snapshot.documents}
        if source_id not in documents:
            raise RelationError("source must be an existing document ID")
        targets = (
            documents
            if kind is RelationKind.LINKS_TO
            else (
                {item.id for item in snapshot.processes}
                if kind is RelationKind.DESCRIBES
                else {item.id for item in snapshot.roles}
            )
        )
        if target_id not in targets:
            raise RelationError("target ID has the wrong type or does not exist")
        if kind is RelationKind.LINKS_TO and source_id == target_id:
            raise RelationError("LINKS_TO cannot reference the same document")

    @staticmethod
    def _validate(snapshot: GraphSnapshot) -> None:
        result = validate_graph(snapshot)
        if not result.is_valid:
            raise RelationError(result.errors[0].message)


def _definition(
    kind: RelationKind,
) -> tuple[str, type[LinksTo] | type[Describes] | type[ForRole]]:
    if kind is RelationKind.LINKS_TO:
        return "links_to", LinksTo
    if kind is RelationKind.DESCRIBES:
        return "describes", Describes
    if kind is RelationKind.FOR_ROLE:
        return "for_roles", ForRole
    raise RelationError("unsupported relation kind")
