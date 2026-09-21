from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mvp_v2.adapters.csv.snapshot import parse_table_model, write_snapshot
from mvp_v2.application.dto.importing import canonical_json_bytes
from mvp_v2.application.manifests import (
    DUMP_FORMAT,
    DumpManifest,
    compute_dump_run_id,
)
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.schema_registry import (
    GRAPH_SCHEMA_VERSION,
    RELATION_SPECS,
    SCHEMA_PROFILE_SHA256,
    TABLE_SPECS,
)
from mvp_v2.domain.snapshot import GraphSnapshot

Row = dict[str, str]


class RestampPlan(BaseModel):
    """Read-only repair plan for a manually edited immutable dump."""

    model_config = ConfigDict(frozen=True)

    dump_path: str
    aborted: bool = False
    problems: tuple[str, ...] = ()
    report_lines: tuple[str, ...] = ()
    tables_to_write: dict[str, bytes] = {}
    resign_manifest: bool = False
    manifest_bytes: bytes = b""


class RestampReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    lines: tuple[str, ...]


class RestampError(ValueError):
    pass


def _read_tables(
    dump: Path, problems: list[str]
) -> dict[str, tuple[tuple[str, ...], list[Row]]]:
    tables: dict[str, tuple[tuple[str, ...], list[Row]]] = {}
    for spec in TABLE_SPECS:
        table_path = dump / f"tables/{spec.table_name}.csv"
        if not table_path.exists() and spec.table_name == "HAS_SUMMARY_EMBEDDING":
            tables[spec.table_name] = (spec.headers, [])
            continue
        raw = table_path.read_text(encoding="utf-8")
        rows = list(csv.reader(io.StringIO(raw)))
        header = tuple(rows[0]) if rows else ()
        body: list[Row] = []
        for row in rows[1:]:
            if len(row) != len(header):
                problems.append(
                    f"{spec.table_name}.csv row with {len(row)} cells does not "
                    f"match the {len(header)}-column header"
                )
                continue
            body.append(dict(zip(header, row, strict=True)))
        tables[spec.table_name] = (header, body)
    return tables


def plan_dump_restamp(dump: Path) -> RestampPlan:
    problems: list[str] = []
    report: list[str] = []
    try:
        manifest = DumpManifest.model_validate_json(
            (dump / "manifest.json").read_bytes()
        )
    except (OSError, ValueError):
        return RestampPlan(
            dump_path=str(dump),
            aborted=True,
            problems=("manifest.json is missing or unreadable",),
        )
    actual_files = {path.name for path in (dump / "tables").glob("*.csv")}
    manifest_files = {Path(item.path).name for item in manifest.files}
    if actual_files != manifest_files:
        problems.append("tables directory does not match the manifest file set")
    tables = _read_tables(dump, problems)

    version_by_id = {row["id"]: row for row in tables["DocumentVersion"][1]}
    doc_to_versions: dict[str, set[str]] = {}
    for edge in tables["HAS_VERSION"][1]:
        if edge["to"] in version_by_id:
            doc_to_versions.setdefault(edge["from"], set()).add(edge["to"])

    doomed_docs: list[str] = []
    doomed_versions: set[str] = set()
    for row in tables["Document"][1]:
        if row["is_active"].lower() != "true":
            continue
        owned = doc_to_versions.get(row["id"], set())
        healthy_current = [
            version
            for version in owned
            if version_by_id[version]["is_current"].lower() == "true"
            and version_by_id[version]["sha256"] != ""
        ]
        if len(healthy_current) == 1:
            continue
        owned_rows = [version_by_id[version] for version in owned]
        if owned_rows and all(
            item["sha256"] == "" and item["error"] != "" for item in owned_rows
        ):
            doomed_docs.append(row["id"])
            doomed_versions |= owned
        else:
            problems.append(
                f"active document {row['id']} needs exactly one current "
                "version; manual fix required"
            )

    removed: dict[str, set[str]] = {
        "Document": set(doomed_docs),
        "DocumentVersion": set(doomed_versions),
    }
    chunk_ids = {row["id"] for row in tables["Chunk"][1]}
    removed["Chunk"] = {
        edge["to"]
        for edge in tables["HAS_CHUNK"][1]
        if edge["from"] in removed["DocumentVersion"]
    } & chunk_ids
    asset_ids = {row["id"] for row in tables["Asset"][1]}
    removed["Asset"] = {
        edge["to"]
        for edge in tables["HAS_ASSET"][1]
        if edge["from"] in removed["Document"]
    } & asset_ids
    embedding_ids = {row["id"] for row in tables["Embedding"][1]}
    removed["Embedding"] = {
        edge["to"]
        for name in ("HAS_EMBEDDING_CHUNK", "HAS_EMBEDDING_ASSET")
        for edge in tables[name][1]
        if edge["from"] in removed["Chunk"] | removed["Asset"]
    } & embedding_ids
    removed["Embedding"] |= {
        edge["to"]
        for edge in tables["HAS_SUMMARY_EMBEDDING"][1]
        if edge["from"] in removed["Document"]
    } & embedding_ids

    candidate_rows: dict[str, list[Row]] = {}
    for spec in TABLE_SPECS:
        if spec in RELATION_SPECS:
            continue
        candidate_rows[spec.table_name] = [
            row
            for row in tables[spec.table_name][1]
            if row["id"] not in removed.get(spec.table_name, set())
        ]
    for spec in RELATION_SPECS:
        source_ids = {row["id"] for row in candidate_rows[spec.source_label]}
        target_ids = {row["id"] for row in candidate_rows[spec.target_label]}
        candidate_rows[spec.table_name] = [
            row
            for row in tables[spec.table_name][1]
            if row["from"] in source_ids and row["to"] in target_ids
        ]

    if doomed_docs:
        report.append("documents removed with cascade: " + ", ".join(doomed_docs))
    for name, identifiers in removed.items():
        if identifiers:
            report.append(f"{name}: {len(identifiers)} row(s) removed")
    for spec in RELATION_SPECS:
        before = len(tables[spec.table_name][1])
        after = len(candidate_rows[spec.table_name])
        if before != after:
            report.append(f"{spec.table_name}: {before - after} edge(s) dropped")

    candidate = _candidate_snapshot(tables, candidate_rows, problems)
    if problems or candidate is None:
        return RestampPlan(
            dump_path=str(dump),
            aborted=True,
            problems=tuple(problems) or ("candidate snapshot failed to build",),
            report_lines=tuple(report),
        )
    validation = validate_graph(candidate)
    if not validation.is_valid:
        return RestampPlan(
            dump_path=str(dump),
            aborted=True,
            problems=tuple(
                f"{issue.severity}: {issue.message}" for issue in validation.errors
            ),
            report_lines=tuple(report),
        )

    with tempfile.TemporaryDirectory(prefix="restamp-") as tmp:
        staged_entries = write_snapshot(Path(tmp), candidate)
        staged_bytes = {
            entry.path: (Path(tmp) / entry.path).read_bytes()
            for entry in staged_entries
        }
    tables_to_write = {
        path: payload
        for path, payload in staged_bytes.items()
        if not (dump / path).is_file() or (dump / path).read_bytes() != payload
    }
    previous_entries = {item.path: item for item in manifest.files}
    resign = any(
        entry.path not in previous_entries
        or (entry.sha256, entry.row_count)
        != (
            previous_entries[entry.path].sha256,
            previous_entries[entry.path].row_count,
        )
        for entry in staged_entries
    ) or len(staged_entries) != len(manifest.files)
    if not tables_to_write and not resign:
        return RestampPlan(dump_path=str(dump), report_lines=())
    report.append(f"tables rewritten: {len(tables_to_write)}")
    report.append(f"manifest re-signed: {'yes' if resign else 'no'}")

    manifest_bytes = b""
    if resign:
        run_id = compute_dump_run_id(
            manifest.dump_number, manifest.published_at, staged_entries
        )
        new_manifest = DumpManifest(
            format=DUMP_FORMAT,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
            schema_profile_sha256=SCHEMA_PROFILE_SHA256,
            dump_number=manifest.dump_number,
            published_at=manifest.published_at,
            run_id=run_id,
            files=staged_entries,
        )
        manifest_bytes = canonical_json_bytes(new_manifest.canonical_payload())
        report.append(f"new run_id: {run_id}")
    return RestampPlan(
        dump_path=str(dump),
        report_lines=tuple(report),
        tables_to_write=tables_to_write,
        resign_manifest=resign,
        manifest_bytes=manifest_bytes,
    )


def _candidate_snapshot(
    tables: dict[str, tuple[tuple[str, ...], list[Row]]],
    candidate_rows: dict[str, list[Row]],
    problems: list[str],
) -> GraphSnapshot | None:
    values: dict[str, object] = {}
    for spec in TABLE_SPECS:
        models = []
        for number, row in enumerate(candidate_rows[spec.table_name], start=2):
            try:
                header = tables[spec.table_name][0]
                encoded = [row[column] for column in header]
                model = parse_table_model(spec, header, encoded, number)
                models.append(model.model_dump(mode="python"))
            except (KeyError, ValueError) as error:
                problems.append(
                    f"{spec.table_name}.csv row {number} is invalid: {error}"
                )
        values[spec.snapshot_field] = tuple(models)
    if problems:
        return None
    return GraphSnapshot.model_validate(values)


def apply_dump_restamp(plan: RestampPlan) -> RestampReport:
    manifest_repair = plan.resign_manifest and bool(plan.manifest_bytes)
    if plan.aborted or (not plan.tables_to_write and not manifest_repair):
        raise RestampError("restamp plan is empty or aborted")
    dump = Path(plan.dump_path)
    for relative, payload in plan.tables_to_write.items():
        (dump / relative).write_bytes(payload)
    if plan.resign_manifest and plan.manifest_bytes:
        (dump / "manifest.json").write_bytes(plan.manifest_bytes)
    from mvp_v2.adapters.ladybug import load_verified_dump

    manifest, _ = load_verified_dump(dump)
    return RestampReport(run_id=manifest.run_id, lines=tuple(plan.report_lines))
