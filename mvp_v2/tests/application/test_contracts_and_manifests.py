from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mvp_v2.application.dto.deletion import (
    DatabaseIdentity,
    DeletionCommand,
    DeletionImpact,
    DeletionResult,
    GraphNodeRef,
    GraphRelationRef,
)
from mvp_v2.application.dto.retrieval import (
    EmbeddingBuildRequest,
    EmbeddingPurpose,
    EmbeddingSpaceIdentity,
    SubjectKind,
    SubjectRef,
    VectorQuery,
)
from mvp_v2.application.manifests import (
    DATABASE_FORMAT,
    DUMP_FORMAT,
    DatabaseManifest,
    DumpFileEntry,
    DumpManifest,
    TableCount,
    compute_database_build_id,
    compute_dump_run_id,
)
from mvp_v2.domain.schema_registry import (
    GRAPH_SCHEMA_VERSION,
    SCHEMA_PROFILE_SHA256,
    TABLE_NAMES,
)

NOW = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
SHA = "b" * 64


def make_space() -> EmbeddingSpaceIdentity:
    return EmbeddingSpaceIdentity(
        provider="local", model="embed", model_version="v1", dimension=2
    )


def make_files() -> tuple[DumpFileEntry, ...]:
    return tuple(
        DumpFileEntry(path=f"tables/{name}.csv", sha256=SHA, row_count=1)
        for name in TABLE_NAMES
    )


def make_counts() -> tuple[TableCount, ...]:
    return tuple(TableCount(table=name, count=1) for name in TABLE_NAMES)


def make_database_manifest(**changes: object) -> DatabaseManifest:
    values: dict[str, object] = {
        "format": DATABASE_FORMAT,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "database_name": "main",
        "built_at": NOW,
        "source_dump_number": 1,
        "source_dump_published_at": NOW,
        "source_dump_run_id": SHA,
        "embedding_space": make_space(),
        "vision_provider": "local",
        "vision_model": "vision",
        "vision_model_version": "v1",
        "row_counts": make_counts(),
    }
    values.update(changes)
    values["build_id"] = compute_database_build_id(
        database_name=values["database_name"],  # type: ignore[arg-type]
        built_at=values["built_at"],  # type: ignore[arg-type]
        source_dump_number=values["source_dump_number"],  # type: ignore[arg-type]
        source_dump_published_at=values["source_dump_published_at"],  # type: ignore[arg-type]
        source_dump_run_id=values["source_dump_run_id"],  # type: ignore[arg-type]
        embedding_space=values["embedding_space"],  # type: ignore[arg-type]
        vision_provider=values["vision_provider"],  # type: ignore[arg-type]
        vision_model=values["vision_model"],  # type: ignore[arg-type]
        vision_model_version=values["vision_model_version"],  # type: ignore[arg-type]
        row_counts=values["row_counts"],  # type: ignore[arg-type]
        graph_schema_version=values["graph_schema_version"],  # type: ignore[arg-type]
        schema_profile_sha256=values.get(
            "schema_profile_sha256", SCHEMA_PROFILE_SHA256
        ),  # type: ignore[arg-type]
    )
    return DatabaseManifest.model_validate(values)


@pytest.mark.parametrize("value", ["unknown", 1, True, None])
def test_dto_enums_reject_unknown_numeric_and_bool(value: object) -> None:
    with pytest.raises(ValidationError):
        SubjectRef.model_validate({"kind": value, "id": "chunk:one"})


@pytest.mark.parametrize("value", ["unknown", 1, True, None])
def test_embedding_purpose_rejects_non_exact_values(value: object) -> None:
    with pytest.raises(ValidationError):
        VectorQuery.model_validate(
            {
                "space": make_space(),
                "vector": (0.1, 0.2),
                "purposes": (value,),
                "limit": 5,
            }
        )


def test_dto_enums_accept_exact_strings_instances_and_json_roundtrip() -> None:
    from_string = SubjectRef.model_validate({"kind": "chunk", "id": "chunk:one"})
    from_enum = SubjectRef(kind=SubjectKind.CHUNK, id="chunk:one")
    assert from_string == from_enum
    query = VectorQuery(
        space=make_space(),
        vector=(0.1, 0.2),
        purposes=(EmbeddingPurpose.CHUNK_RETRIEVAL,),
        limit=5,
    )
    assert VectorQuery.model_validate_json(query.model_dump_json()) == query
    assert query.model_dump(mode="json")["purposes"] == ["chunk_retrieval"]


def test_build_request_is_nonempty_and_identity_text_is_required() -> None:
    subject = SubjectRef.model_validate({"kind": "asset", "id": "asset:one"})
    assert EmbeddingBuildRequest(
        subjects=(subject,), space=make_space(), rebuild_all=False
    ).subjects == (subject,)
    with pytest.raises(ValidationError):
        EmbeddingBuildRequest(subjects=(), space=make_space(), rebuild_all=False)
    with pytest.raises(ValidationError):
        EmbeddingSpaceIdentity(
            provider=" ", model="embed", model_version="v1", dimension=2
        )


def test_dump_manifest_requires_exact_registry_files_and_canonical_order() -> None:
    files = make_files()
    run_id = compute_dump_run_id(1, NOW, files)
    assert run_id == compute_dump_run_id(1, NOW, tuple(reversed(files)))
    manifest = DumpManifest(
        format=DUMP_FORMAT,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        schema_profile_sha256=SCHEMA_PROFILE_SHA256,
        dump_number=1,
        published_at=NOW,
        run_id=run_id,
        files=files,
    )
    assert manifest.model_dump(mode="json")["published_at"].endswith("Z")
    for bad_files in (
        files[:-1],
        (
            *files[:-1],
            DumpFileEntry(path="tables/UNKNOWN.csv", sha256=SHA, row_count=1),
        ),
        (*files[:-1], files[0]),
    ):
        with pytest.raises(ValidationError):
            DumpManifest.model_validate({**manifest.model_dump(), "files": bad_files})


def test_manifest_schema_and_identity_changes_invalidate_hashes() -> None:
    files = make_files()
    manifest_values = {
        "format": DUMP_FORMAT,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "dump_number": 1,
        "published_at": NOW,
        "run_id": compute_dump_run_id(1, NOW, files),
        "files": files,
    }
    for retired_version in ("mvp-v2-graph-v1", "other"):
        with pytest.raises(ValidationError):
            DumpManifest.model_validate(
                {**manifest_values, "graph_schema_version": retired_version}
            )
    database = make_database_manifest()
    with pytest.raises(ValidationError):
        DatabaseManifest.model_validate(
            {**database.model_dump(), "graph_schema_version": "mvp-v2-graph-v1"}
        )
    with pytest.raises(ValidationError):
        DatabaseManifest.model_validate(
            {**database.model_dump(), "source_dump_run_id": "c" * 64}
        )


def test_database_manifest_requires_exact_counts_and_canonical_build_id() -> None:
    database = make_database_manifest()
    reversed_counts = tuple(reversed(database.row_counts))
    reordered = make_database_manifest(row_counts=reversed_counts)
    assert reordered.build_id == database.build_id
    bad_counts = (*database.row_counts[:-1], TableCount(table="UNKNOWN", count=1))
    with pytest.raises(ValidationError):
        make_database_manifest(row_counts=database.row_counts[:-1])
    with pytest.raises(ValidationError):
        make_database_manifest(row_counts=bad_counts)
    with pytest.raises(ValidationError):
        make_database_manifest(database_name=" ")


def make_command() -> DeletionCommand:
    return DeletionCommand(
        target=GraphNodeRef(table_name="Document", id="document:one")
    )


def test_deletion_impact_uses_exact_typed_refs() -> None:
    command = make_command()
    impact = DeletionImpact(
        command=command,
        affected_nodes=(command.target,),
        affected_relations=(
            GraphRelationRef(
                relation_type="LINKS_TO",
                source_id="document:one",
                target_id="document:two",
            ),
        ),
        referenced_physical_paths=("media/a.png",),
        promoted_version_ids=("version:one",),
        deactivated_document_ids=(),
        stale_databases=(DatabaseIdentity(database_name="main", build_id=SHA),),
    )
    assert impact.affected_relations[0].relation_type == "LINKS_TO"
    with pytest.raises(ValidationError):
        GraphNodeRef.model_validate({"table_name": "Unknown", "id": "x"})
    with pytest.raises(ValidationError):
        DeletionCommand(target=GraphNodeRef(table_name="Role", id="role:one"))


@pytest.mark.parametrize(
    ("status", "dump_number", "active", "error", "valid"),
    [
        ("completed", 2, True, "", True),
        ("completed", None, True, "", False),
        ("completed", 2, False, "", False),
        ("completed", 2, True, "error", False),
        ("pending", None, False, "rebuild pending", True),
        ("pending", 2, False, "dump published; rebuild pending", True),
        ("pending", None, True, "old DB remains active", True),
        ("pending", 2, True, "dump published; old DB remains active", True),
        ("failed", None, False, "validation failed", True),
        ("failed", 2, False, "dump published; rebuild failed", True),
        ("failed", None, True, "old DB remains active", True),
        ("failed", 2, True, "dump published; old DB remains active", True),
        ("pending", 2, True, "", False),
        ("failed", 2, True, "   ", False),
    ],
)
def test_deletion_result_state_matrix(
    status: str, dump_number: int | None, active: bool, error: str, valid: bool
) -> None:
    active_database = (
        DatabaseIdentity(database_name="main", build_id=SHA) if active else None
    )
    values = {
        "command": make_command(),
        "status": status,
        "published_dump_number": dump_number,
        "active_database": active_database,
        "stale_databases": (DatabaseIdentity(database_name="old", build_id="c" * 64),),
        "error": error,
    }
    if valid:
        assert DeletionResult.model_validate(values).status == status
    else:
        with pytest.raises(ValidationError):
            DeletionResult.model_validate(values)
