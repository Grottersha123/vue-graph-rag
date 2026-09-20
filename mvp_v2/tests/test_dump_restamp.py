from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from mvp_v2.adapters.csv.snapshot import write_snapshot
from mvp_v2.adapters.ladybug import load_verified_dump
from mvp_v2.application.dto.importing import canonical_json_bytes
from mvp_v2.application.dump_restamp import (
    RestampError,
    apply_dump_restamp,
    plan_dump_restamp,
)
from mvp_v2.application.manifests import (
    DUMP_FORMAT,
    DumpManifest,
    compute_dump_run_id,
)
from mvp_v2.domain.schema_registry import RELATION_SPECS
from tests.factories import build_valid_snapshot

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def _build_dump(tmp_path: Path) -> tuple[Path, str]:
    dump = tmp_path / "dump-000007"
    entries = write_snapshot(dump, build_valid_snapshot())
    run_id = compute_dump_run_id(7, NOW, entries)
    manifest = DumpManifest(
        format=DUMP_FORMAT,
        graph_schema_version="mvp-v2-graph-v1",
        dump_number=7,
        published_at=NOW,
        run_id=run_id,
        files=entries,
    )
    (dump / "manifest.json").write_bytes(
        canonical_json_bytes(manifest.model_dump(mode="json"))
    )
    return dump, run_id


def _rows(dump: Path, table: str) -> list[dict[str, str]]:
    raw = (dump / f"tables/{table}.csv").read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


def _rewrite(dump: Path, table: str, rows: list[dict[str, str]]) -> None:
    raw = (dump / f"tables/{table}.csv").read_text(encoding="utf-8")
    header = list(csv.reader(io.StringIO(raw)))[0]
    with (dump / f"tables/{table}.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream, dialect="excel", lineterminator="\r\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([row[column] for column in header])


def test_healthy_dump_needs_no_repairs(tmp_path: Path) -> None:
    dump, _ = _build_dump(tmp_path)

    plan = plan_dump_restamp(dump)

    assert not plan.aborted
    assert plan.report_lines == ()
    assert plan.tables_to_write == {}


def test_orphan_edge_is_dropped_without_resign(tmp_path: Path) -> None:
    dump, run_id = _build_dump(tmp_path)
    links = _rows(dump, "LINKS_TO")
    links.append({"from": "document", "to": "missing-document"})
    _rewrite(dump, "LINKS_TO", links)
    manifest_before = (dump / "manifest.json").read_bytes()

    plan = plan_dump_restamp(dump)
    report = apply_dump_restamp(plan)

    assert any("LINKS_TO: 1 edge(s) dropped" in line for line in plan.report_lines)
    assert plan.resign_manifest is False
    assert "manifest re-signed: no" in plan.report_lines
    assert (dump / "manifest.json").read_bytes() == manifest_before
    dump_manifest, snapshot = load_verified_dump(dump)
    assert dump_manifest.run_id == run_id == report.run_id
    links_field = next(
        spec.snapshot_field for spec in RELATION_SPECS if spec.table_name == "LINKS_TO"
    )
    assert len(getattr(snapshot, links_field)) == len(links) - 1


def test_errored_artifact_document_is_removed_with_cascade(tmp_path: Path) -> None:
    dump, _ = _build_dump(tmp_path)
    docs = _rows(dump, "Document")
    docs.append(
        {
            "id": "doc-broken",
            "title": "broken",
            "source_name": "",
            "content_format": "markdown",
            "source_url": "",
            "external_id": "",
            "is_active": "true",
        }
    )
    _rewrite(dump, "Document", docs)
    versions = _rows(dump, "DocumentVersion")
    versions.append(
        {
            "id": "doc-broken:v:error",
            "version": "1",
            "sha256": "",
            "local_path": "",
            "created_at": "2026-09-15T10:00:00.000000Z",
            "is_current": "true",
            "error": "extraction_content_too_short",
        }
    )
    _rewrite(dump, "DocumentVersion", versions)
    edges = _rows(dump, "HAS_VERSION")
    edges.append({"from": "doc-broken", "to": "doc-broken:v:error"})
    _rewrite(dump, "HAS_VERSION", edges)
    statuses = _rows(dump, "HAS_STATUS")
    statuses.append({"from": "doc-broken:v:error", "to": "downloaded"})
    _rewrite(dump, "HAS_STATUS", statuses)

    plan = plan_dump_restamp(dump)
    report = apply_dump_restamp(plan)

    assert any("doc-broken" in line for line in plan.report_lines)
    dump_manifest, snapshot = load_verified_dump(dump)
    assert {item.id for item in snapshot.documents}.isdisjoint({"doc-broken"})
    assert report.run_id == dump_manifest.run_id


def test_content_edit_requires_resign_and_dump_loads(tmp_path: Path) -> None:
    dump, run_id = _build_dump(tmp_path)
    docs = _rows(dump, "Document")
    docs[0]["title"] = "Renamed by hand"
    _rewrite(dump, "Document", docs)
    links = _rows(dump, "LINKS_TO")
    links.append({"from": "document", "to": "missing-document"})
    _rewrite(dump, "LINKS_TO", links)

    plan = plan_dump_restamp(dump)
    report = apply_dump_restamp(plan)

    assert plan.resign_manifest is True
    assert "manifest re-signed: yes" in plan.report_lines
    dump_manifest, snapshot = load_verified_dump(dump)
    assert dump_manifest.run_id != run_id
    assert dump_manifest.run_id == report.run_id
    titles = {item.id: item.title for item in snapshot.documents}
    assert titles["document"] == "Renamed by hand"


def test_inactive_document_edit_allows_manifest_only_resign(tmp_path: Path) -> None:
    dump, run_id = _build_dump(tmp_path)
    documents = _rows(dump, "Document")
    documents[0]["is_active"] = "false"
    _rewrite(dump, "Document", documents)

    plan = plan_dump_restamp(dump)
    report = apply_dump_restamp(plan)

    assert plan.tables_to_write == {}
    assert plan.resign_manifest is True
    assert "tables rewritten: 0" in plan.report_lines
    manifest, snapshot = load_verified_dump(dump)
    assert manifest.run_id == report.run_id
    assert manifest.run_id != run_id
    assert snapshot.documents[0].is_active is False


def test_missing_current_version_without_error_blocks_restamp(
    tmp_path: Path,
) -> None:
    dump, _ = _build_dump(tmp_path)
    versions = _rows(dump, "DocumentVersion")
    for row in versions:
        row["is_current"] = "false"
    _rewrite(dump, "DocumentVersion", versions)

    plan = plan_dump_restamp(dump)

    assert plan.aborted
    assert any("current" in problem for problem in plan.problems)
    assert plan.tables_to_write == {}


def test_manifest_is_missing(tmp_path: Path) -> None:
    dump, _ = _build_dump(tmp_path)
    (dump / "manifest.json").write_text("{}")

    plan = plan_dump_restamp(dump)

    assert plan.aborted
    assert "manifest.json" in plan.problems[0]


def test_apply_refuses_empty_plan(tmp_path: Path) -> None:
    dump, _ = _build_dump(tmp_path)
    plan = plan_dump_restamp(dump)

    try:
        apply_dump_restamp(plan)
    except RestampError:
        pass
    else:
        raise AssertionError("expected RestampError")
