from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from mvp_v2.domain.base import (
    AllowEmptyText,
    ContractModel,
    EntityId,
    NonBlankText,
    RelativePath,
    Sha256,
)
from mvp_v2.domain.schema_registry import GraphNodeTableName, GraphRelationType

PositiveInt = Annotated[int, Field(strict=True, ge=1, le=9_223_372_036_854_775_807)]
_DELETABLE_TABLES = frozenset({"Document", "DocumentVersion", "Asset", "Embedding"})


class GraphNodeRef(ContractModel):
    table_name: GraphNodeTableName
    id: EntityId


class GraphRelationRef(ContractModel):
    relation_type: GraphRelationType
    source_id: EntityId
    target_id: EntityId


class DatabaseIdentity(ContractModel):
    database_name: NonBlankText
    build_id: Sha256


class DeletionCommand(ContractModel):
    target: GraphNodeRef

    @model_validator(mode="after")
    def validate_target(self) -> DeletionCommand:
        if self.target.table_name not in _DELETABLE_TABLES:
            raise ValueError("node table is not a supported deletion target")
        return self


class DeletionImpact(ContractModel):
    command: DeletionCommand
    affected_nodes: tuple[GraphNodeRef, ...]
    affected_relations: tuple[GraphRelationRef, ...]
    referenced_physical_paths: tuple[RelativePath, ...]
    promoted_version_ids: tuple[EntityId, ...]
    deactivated_document_ids: tuple[EntityId, ...]
    stale_databases: tuple[DatabaseIdentity, ...]

    @model_validator(mode="after")
    def validate_impact(self) -> DeletionImpact:
        if self.command.target not in self.affected_nodes:
            raise ValueError("affected nodes must include the deletion target")
        collections = (
            self.affected_nodes,
            self.affected_relations,
            self.referenced_physical_paths,
            self.promoted_version_ids,
            self.deactivated_document_ids,
            self.stale_databases,
        )
        if any(len(items) != len(set(items)) for items in collections):
            raise ValueError("deletion impact collections must not contain duplicates")
        return self


class DeletionResult(ContractModel):
    command: DeletionCommand
    status: Literal["completed", "pending", "failed"]
    published_dump_number: PositiveInt | None = None
    active_database: DatabaseIdentity | None = None
    stale_databases: tuple[DatabaseIdentity, ...] = ()
    error: AllowEmptyText = ""

    @model_validator(mode="after")
    def validate_outcome(self) -> DeletionResult:
        if self.status == "completed":
            if (
                self.published_dump_number is None
                or self.active_database is None
                or self.error
            ):
                raise ValueError(
                    "completed deletion requires dump and active DB, without error"
                )
        elif not self.error or self.error.isspace():
            raise ValueError("pending or failed deletion requires an explanation")
        return self
