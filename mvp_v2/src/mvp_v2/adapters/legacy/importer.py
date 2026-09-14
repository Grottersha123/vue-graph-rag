from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mvp_v2.adapters.legacy.common import LegacyTools
from mvp_v2.adapters.legacy.diagnostics import LegacyDiagnostics
from mvp_v2.adapters.legacy.media_plan import build_media_plan
from mvp_v2.adapters.legacy.parsers import LegacyParser
from mvp_v2.adapters.legacy.reader import EXPECTED_HEADERS, LegacyTable, read_table
from mvp_v2.adapters.legacy.reporting import build_import_report
from mvp_v2.application.dto.importing import ImportIssue, LegacyImportResult
from mvp_v2.domain import graph
from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.snapshot import GraphSnapshot


@dataclass(frozen=True, slots=True)
class _MappedLegacy:
    snapshot: GraphSnapshot
    error_rules: int
    manual_resolved: int
    manual_unresolved: int


class CsvLegacyImporter(LegacyTools):
    """Read-only anti-corruption adapter for the exact legacy CSV contract."""

    def preview(self, source_root: Path) -> LegacyImportResult:
        tables = {name: read_table(source_root, name) for name in EXPECTED_HEADERS}
        issues = [issue for table in tables.values() for issue in table.issues]
        diagnostics = LegacyDiagnostics()
        diagnostics._ignored_backups(source_root, issues)
        error_documents = self._error_documents(tables["HAS_ERROR"], issues)
        mapped = self._map(tables, error_documents, issues)
        return self._finish(
            source_root, tables, error_documents, mapped, diagnostics, issues
        )

    def _error_documents(
        self, table: LegacyTable, issues: list[ImportIssue]
    ) -> set[str]:
        markers = Counter((row["from"], row["to"]) for _, row in table.rows)
        for (source_id, target_id), count in sorted(markers.items()):
            edge_id = self._safe_edge(source_id, target_id)
            if target_id != "error":
                issues.append(
                    self._issue(
                        "warning",
                        "LEGACY_ERROR_TARGET_INVALID",
                        "legacy_error",
                        "HAS_ERROR",
                        None,
                        "to",
                        edge_id,
                        "HAS_ERROR target is not the legacy error marker",
                    )
                )
            if count > 1:
                issues.append(
                    self._issue(
                        "warning",
                        "LEGACY_ERROR_DUPLICATE",
                        "legacy_error",
                        "HAS_ERROR",
                        None,
                        "",
                        edge_id,
                        f"legacy error marker occurs {count} times",
                    )
                )
        return {source_id for source_id, target_id in markers if target_id == "error"}

    def _map(
        self,
        tables: dict[str, LegacyTable],
        error_documents: set[str],
        issues: list[ImportIssue],
    ) -> _MappedLegacy:
        parser = LegacyParser()
        version_owners = {
            row["to"]: row["from"] for _, row in tables["HAS_VERSION"].rows
        }
        source_types = parser._models(tables["SourceType"], graph.SourceType, issues)
        documents = parser._documents(tables["Document"], issues)
        versions, error_rules = parser._versions(
            tables["DocumentVersion"], version_owners, error_documents, issues
        )
        assets = parser._assets(tables["Asset"], issues)
        roles = parser._models(tables["Role"], graph.Role, issues)
        statuses = parser._models(tables["Status"], graph.Status, issues)
        has_versions = parser._relations(
            tables["HAS_VERSION"], graph.HasVersion, issues
        )
        has_assets = parser._relations(tables["HAS_ASSET"], graph.HasAsset, issues)
        has_statuses = parser._relations(tables["HAS_STATUS"], graph.HasStatus, issues)
        for_roles = parser._relations(tables["FOR_ROLE"], graph.ForRole, issues)
        links = list(parser._relations(tables["LINKS_TO"], graph.LinksTo, issues))
        merged_links, resolved, unresolved = self._merge_manual_links(
            tables["manual_links"], documents, links, issues
        )
        snapshot = GraphSnapshot(
            source_types=source_types,
            documents=documents,
            document_versions=versions,
            assets=assets,
            roles=roles,
            statuses=statuses,
            has_source_types=parser._reverse_source_edges(
                tables["HAS_DOCUMENT"], issues
            ),
            has_versions=has_versions,
            supersedes=parser._build_supersedes(versions, has_versions, issues),
            has_statuses=has_statuses,
            has_assets=has_assets,
            links_to=merged_links,
            for_roles=for_roles,
        )
        return _MappedLegacy(snapshot, error_rules, resolved, unresolved)

    def _merge_manual_links(
        self,
        table: LegacyTable,
        documents: tuple[graph.Document, ...],
        links: list[graph.LinksTo],
        issues: list[ImportIssue],
    ) -> tuple[tuple[graph.LinksTo, ...], int, int]:
        document_ids = {item.id for item in documents}
        document_by_url = {
            item.source_url: item.id for item in documents if item.source_url
        }
        existing = {(item.source_id, item.target_id) for item in links}
        resolved = 0
        unresolved = 0
        for row_number, row in table.rows:
            source_id = row["source_document_id"]
            target_id = row["target_document_id"]
            edge = (source_id, target_id)
            url_target = document_by_url.get(row["target_url"])
            if row["link_type"] != "LINKS_TO":
                unresolved += 1
                self._manual_issue(
                    issues,
                    "MANUAL_LINK_TYPE",
                    row_number,
                    source_id,
                    "manual link type is not LINKS_TO",
                    "link_type",
                )
            elif (
                target_id not in document_ids
                and source_id in document_ids
                and url_target is not None
                and (source_id, url_target) in existing
            ):
                self._manual_issue(
                    issues,
                    "MANUAL_LINK_STALE_ID_REDUNDANT",
                    row_number,
                    self._safe_edge(source_id, target_id),
                    "URL evidence points to an existing link; no edge added",
                    "target_document_id",
                    severity="info",
                )
            elif source_id not in document_ids or target_id not in document_ids:
                unresolved += 1
                self._manual_issue(
                    issues,
                    "MANUAL_LINK_UNRESOLVED",
                    row_number,
                    self._safe_edge(source_id, target_id),
                    "manual link endpoints do not resolve to exact Document IDs",
                    "",
                )
            elif edge not in existing:
                links.append(graph.LinksTo(source_id=source_id, target_id=target_id))
                existing.add(edge)
                resolved += 1
        return tuple(links), resolved, unresolved

    def _manual_issue(
        self,
        issues: list[ImportIssue],
        code: str,
        row: int,
        record_id: str,
        message: str,
        column: str,
        *,
        severity: Literal["warning", "info"] = "warning",
    ) -> None:
        level: Literal["warning", "info"] = "info" if severity == "info" else "warning"
        issues.append(
            self._issue(
                level,
                code,
                "link",
                "manual_links",
                row,
                column,
                self._safe_id(record_id),
                message,
            )
        )

    def _finish(
        self,
        source_root: Path,
        tables: dict[str, LegacyTable],
        error_documents: set[str],
        mapped: _MappedLegacy,
        diagnostics: LegacyDiagnostics,
        issues: list[ImportIssue],
    ) -> LegacyImportResult:
        snapshot = mapped.snapshot
        diagnostics._graph_diagnostics(snapshot, issues)
        diagnostics._cross_checks(tables, snapshot, error_documents, issues)
        issues.append(
            self._issue(
                "info",
                "LEGACY_ERROR_EDGE_DROPPED_RECOVERED",
                "legacy_error",
                "HAS_ERROR",
                None,
                "",
                "",
                f"dropped {len(error_documents)} markers; version text/status retained",
            )
        )
        manifest_status, manifest_reason = diagnostics._manifest_diagnostics(
            tables["image_manifest"], snapshot, issues
        )
        copy_plan = build_media_plan(source_root, snapshot, issues)
        for item in validate_graph(snapshot).issues:
            issues.append(
                self._issue(
                    "error",
                    item.code,
                    "graph_validation",
                    item.table,
                    None,
                    "",
                    item.record_id,
                    item.message,
                )
            )
        without_process = tuple(sorted(item.id for item in snapshot.documents))
        for document_id in without_process:
            issues.append(
                self._issue(
                    "warning",
                    "PROCESS_ASSIGNMENT_REQUIRED",
                    "operator_assignment",
                    "Document",
                    None,
                    "",
                    document_id,
                    "Document has no Process assignment",
                )
            )
        report = build_import_report(
            source_facts=tuple(
                table.fact for table in tables.values() if table.fact is not None
            ),
            snapshot=snapshot,
            issues=issues,
            copy_plan=copy_plan,
            error_rules=mapped.error_rules,
            legacy_error_rows=len(tables["HAS_ERROR"].rows),
            manual_resolved=mapped.manual_resolved,
            manual_unresolved=mapped.manual_unresolved,
            manifest_status=manifest_status,
            manifest_reason=manifest_reason,
            documents_without_process=without_process,
        )
        return LegacyImportResult(snapshot=snapshot, report=report, copy_plan=copy_plan)
