from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from vue_rag.domain import graph
from vue_rag.domain.schema_registry import NODE_SPECS
from vue_rag.domain.snapshot import GraphSnapshot


class Identified(Protocol):
    id: str


IdentifiedType = TypeVar("IdentifiedType", bound=Identified)


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphIndexes:
    node_ids: dict[str, frozenset[str]]
    version_by_id: dict[str, graph.DocumentVersion]
    status_by_id: dict[str, graph.Status]
    embedding_by_id: dict[str, graph.Embedding]
    version_documents: dict[str, tuple[str, ...]]
    chunk_versions: dict[str, tuple[str, ...]]
    asset_versions: dict[str, tuple[str, ...]]


def build_indexes(snapshot: GraphSnapshot) -> GraphIndexes:
    node_ids: dict[str, frozenset[str]] = {}
    for table_spec in NODE_SPECS:
        models = snapshot.__getattribute__(table_spec.snapshot_field)
        node_ids[table_spec.table_name] = frozenset(model_id(model) for model in models)
    return GraphIndexes(
        node_ids=node_ids,
        version_by_id=build_unique_map(snapshot.document_versions),
        status_by_id=build_unique_map(snapshot.statuses),
        embedding_by_id=build_unique_map(snapshot.embeddings),
        version_documents=index_incoming(snapshot.has_versions),
        chunk_versions=index_incoming(snapshot.has_chunks),
        asset_versions=index_incoming(snapshot.has_assets),
    )


def build_unique_map(items: tuple[IdentifiedType, ...]) -> dict[str, IdentifiedType]:
    grouped: dict[str, list[IdentifiedType]] = defaultdict(list)
    for item in items:
        grouped[item.id].append(item)
    return {
        identifier: matches[0]
        for identifier, matches in grouped.items()
        if len(matches) == 1
    }


def model_id(model: BaseModel) -> str:
    identifier = model.model_dump()["id"]
    if not isinstance(identifier, str):
        raise TypeError("registry node id must be a string")
    return identifier


def index_incoming(relations: tuple[graph.Relation, ...]) -> dict[str, tuple[str, ...]]:
    collected: dict[str, list[str]] = defaultdict(list)
    for relation in relations:
        collected[relation.target_id].append(relation.source_id)
    return {target: tuple(sources) for target, sources in collected.items()}


def index_outgoing(relations: tuple[graph.Relation, ...]) -> dict[str, tuple[str, ...]]:
    collected: dict[str, list[str]] = defaultdict(list)
    for relation in relations:
        collected[relation.source_id].append(relation.target_id)
    return {source: tuple(targets) for source, targets in collected.items()}
