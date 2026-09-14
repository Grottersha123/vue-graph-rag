from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from mvp_v2.adapters.legacy.common import LegacyTools
from mvp_v2.adapters.legacy.reader import LegacyTable
from mvp_v2.application.dto.importing import ImportIssue
from mvp_v2.domain import graph
from mvp_v2.domain.snapshot import GraphSnapshot


class LegacyDiagnostics(LegacyTools):
    def _ignored_backups(self, root: Path, issues: list[ImportIssue]) -> None:
        try:
            names = sorted(
                item.name
                for item in root.iterdir()
                if (item.name.endswith(".backup") or ".before-" in item.name)
                and item.is_file()
            )
        except OSError:
            return
        for name in names:
            issues.append(
                self._issue(
                    "info",
                    "CSV_BACKUP_IGNORED",
                    "source",
                    "legacy_root",
                    None,
                    "",
                    self._safe_id(name),
                    "backup-like CSV was ignored and not opened",
                )
            )

    def _graph_diagnostics(
        self, snapshot: GraphSnapshot, issues: list[ImportIssue]
    ) -> None:
        versions = {item.id: item for item in snapshot.document_versions}
        grouped_versions: dict[str, list[graph.DocumentVersion]] = defaultdict(list)
        for edge in snapshot.has_versions:
            version = versions.get(edge.target_id)
            if version is not None:
                grouped_versions[edge.source_id].append(version)
        for _document_id, owned in sorted(grouped_versions.items()):
            numbers = {item.version for item in owned}
            for item in owned:
                if item.version > 1 and not any(
                    number < item.version for number in numbers
                ):
                    issues.append(
                        self._issue(
                            "warning",
                            "VERSION_HISTORY_GAP",
                            "history",
                            "DocumentVersion",
                            None,
                            "version",
                            self._safe_id(item.id),
                            (
                                "version has no lower imported predecessor; "
                                "no edge invented"
                            ),
                        )
                    )
        role_documents = {item.source_id for item in snapshot.for_roles}
        for document in snapshot.documents:
            if document.id not in role_documents:
                issues.append(
                    self._issue(
                        "warning",
                        "DOCUMENT_WITHOUT_ROLE",
                        "operator_assignment",
                        "Document",
                        None,
                        "",
                        document.id,
                        "Document has no Role assignment",
                    )
                )
        for link_edge in snapshot.links_to:
            if link_edge.source_id == link_edge.target_id:
                issues.append(
                    self._issue(
                        "warning",
                        "SELF_LINK_REVIEW",
                        "link",
                        "LINKS_TO",
                        None,
                        "",
                        link_edge.source_id,
                        "self-link was preserved for operator review",
                    )
                )
        assets = {item.id: item for item in snapshot.assets}
        by_owner: dict[str, list[int]] = defaultdict(list)
        for asset_edge in snapshot.has_assets:
            asset = assets.get(asset_edge.target_id)
            if asset is not None:
                by_owner[asset_edge.source_id].append(asset.position)
        for owner, positions in sorted(by_owner.items()):
            counts = Counter(positions)
            if any(count > 1 for count in counts.values()):
                issues.append(
                    self._issue(
                        "warning",
                        "ASSET_POSITION_DUPLICATE",
                        "asset_position",
                        "HAS_ASSET",
                        None,
                        "",
                        self._safe_id(owner),
                        "asset positions contain duplicates and were preserved",
                    )
                )
            ordered = sorted(positions)
            if ordered and ordered != list(range(len(ordered))):
                issues.append(
                    self._issue(
                        "warning",
                        "ASSET_POSITION_GAP",
                        "asset_position",
                        "HAS_ASSET",
                        None,
                        "",
                        self._safe_id(owner),
                        "asset positions contain gaps and were preserved",
                    )
                )

    def _cross_checks(
        self,
        tables: dict[str, LegacyTable],
        snapshot: GraphSnapshot,
        error_docs: set[str],
        issues: list[ImportIssue],
    ) -> None:
        source_by_document = {
            edge.source_id: edge.target_id for edge in snapshot.has_source_types
        }
        source_code = {item.id: item.code for item in snapshot.source_types}
        versions_by_document: dict[str, list[graph.DocumentVersion]] = defaultdict(list)
        version_by_id = {item.id: item for item in snapshot.document_versions}
        for edge in snapshot.has_versions:
            if edge.target_id in version_by_id:
                versions_by_document[edge.source_id].append(
                    version_by_id[edge.target_id]
                )
        status_by_id = {item.id: item.name for item in snapshot.statuses}
        version_status = {
            edge.source_id: status_by_id.get(edge.target_id, "")
            for edge in snapshot.has_statuses
        }
        source_formats: Counter[str] = Counter()
        for number, row in tables["Document"].rows:
            document_id = row["id"]
            source_formats[self._safe_category(row["source_format"])] += 1
            owned = source_code.get(source_by_document.get(document_id, ""), "")
            if owned and row["source_type"] != owned:
                issues.append(
                    self._issue(
                        "error",
                        "SOURCE_TYPE_MISMATCH",
                        "cross_check",
                        "Document",
                        number,
                        "source_type",
                        self._safe_id(document_id),
                        "redundant source_type differs from HAS_DOCUMENT ownership",
                    )
                )
            currents = [
                item
                for item in versions_by_document.get(document_id, ())
                if item.is_current
            ]
            if len(currents) == 1:
                current = currents[0]
                for field in ("sha256", "local_path"):
                    legacy_value = row[field]
                    if field == "local_path" and legacy_value:
                        try:
                            legacy_value = self._rewrite_path(
                                legacy_value, "mvp/mvp_data/documents/", "documents/"
                            )
                        except ValueError:
                            legacy_value = "<invalid>"
                    if legacy_value != getattr(current, field):
                        issues.append(
                            self._issue(
                                "warning",
                                "REDUNDANT_FIELD_MISMATCH",
                                "cross_check",
                                "Document",
                                number,
                                field,
                                self._safe_id(document_id),
                                (
                                    f"legacy Document.{field} differs from owned "
                                    "current version"
                                ),
                            )
                        )
                current_status = version_status.get(current.id, "")
                if current_status and row["status"] != current_status:
                    issues.append(
                        self._issue(
                            "warning",
                            "REDUNDANT_FIELD_MISMATCH",
                            "cross_check",
                            "Document",
                            number,
                            "status",
                            self._safe_id(document_id),
                            (
                                "legacy Document.status differs from owned current "
                                "version status"
                            ),
                        )
                    )
        for source_format, count in sorted(source_formats.items()):
            issues.append(
                self._issue(
                    "info",
                    "UNMAPPED_SOURCE_FORMAT",
                    "dropped_field",
                    "Document",
                    None,
                    "source_format",
                    source_format,
                    f"unmapped source_format was dropped from {count} rows",
                )
            )
        for document_id in sorted(error_docs):
            error_versions = versions_by_document.get(document_id, [])
            if not error_versions:
                issues.append(
                    self._issue(
                        "warning",
                        "LEGACY_ERROR_UNRESOLVED",
                        "legacy_error",
                        "HAS_ERROR",
                        None,
                        "",
                        self._safe_id(document_id),
                        "HAS_ERROR Document has no resolved owned version",
                    )
                )
            elif not any(
                item.error or version_status.get(item.id) == "error"
                for item in error_versions
            ):
                issues.append(
                    self._issue(
                        "warning",
                        "LEGACY_ERROR_MISMATCH",
                        "legacy_error",
                        "HAS_ERROR",
                        None,
                        "",
                        self._safe_id(document_id),
                        (
                            "HAS_ERROR marker is not reflected by owned version "
                            "error/status"
                        ),
                    )
                )

    def _manifest_diagnostics(
        self, table: LegacyTable, snapshot: GraphSnapshot, issues: list[ImportIssue]
    ) -> tuple[Counter[str], Counter[str]]:
        statuses: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        asset_by_id = {item.id: item for item in snapshot.assets}
        evidence: Counter[tuple[str, str, str]] = Counter()
        asset_evidence: Counter[str] = Counter()
        for number, row in table.rows:
            statuses[self._safe_category(row["status"])] += 1
            reasons[self._safe_category(row["reason"])] += 1
            asset_id = row["asset_id"]
            if not asset_id and row["status"] == "manual_pending":
                issues.append(
                    self._issue(
                        "warning",
                        "IMAGE_FETCH_FAILURE",
                        "image_manifest",
                        table.name,
                        number,
                        "reason",
                        self._safe_id(row["source_document_id"]),
                        (
                            "image fetch failed; status/reason retained in "
                            "aggregate counts"
                        ),
                    )
                )
            if not asset_id:
                continue
            evidence[(asset_id, row["local_path"], row["sha256"])] += 1
            asset_evidence[asset_id] += 1
            asset = asset_by_id.get(asset_id)
            if asset is None:
                issues.append(
                    self._issue(
                        "warning",
                        "MANIFEST_ASSET_MISSING",
                        "image_manifest",
                        table.name,
                        number,
                        "asset_id",
                        self._safe_id(asset_id),
                        "manifest asset ID does not exist in Asset.csv target rows",
                    )
                )
                continue
            comparisons = (
                ("sha256", row["sha256"], asset.sha256),
                ("mime_type", row["mime_type"], asset.mime_type),
            )
            for column, observed, expected in comparisons:
                if observed and observed != expected:
                    issues.append(
                        self._issue(
                            "warning",
                            "MANIFEST_ASSET_MISMATCH",
                            "image_manifest",
                            table.name,
                            number,
                            column,
                            self._safe_id(asset_id),
                            f"manifest {column} differs from Asset.csv",
                        )
                    )
            try:
                manifest_path = self._rewrite_path(
                    row["local_path"], "mvp/mvp_data/images/", "images/"
                )
            except ValueError:
                manifest_path = "<invalid>"
            if row["local_path"] and manifest_path != asset.local_path:
                issues.append(
                    self._issue(
                        "warning",
                        "MANIFEST_ASSET_MISMATCH",
                        "image_manifest",
                        table.name,
                        number,
                        "local_path",
                        self._safe_id(asset_id),
                        "manifest path differs from Asset.csv",
                    )
                )
        for (asset_id, _, _), count in sorted(evidence.items()):
            if count > 1:
                issues.append(
                    self._issue(
                        "warning",
                        "MANIFEST_DUPLICATE_EVIDENCE",
                        "image_manifest",
                        table.name,
                        None,
                        "",
                        self._safe_id(asset_id),
                        f"identical asset evidence occurs {count} times",
                    )
                )
        for asset_id, count in sorted(asset_evidence.items()):
            if count > 1:
                issues.append(
                    self._issue(
                        "warning",
                        "MANIFEST_DUPLICATE_ASSET_EVIDENCE",
                        "image_manifest",
                        table.name,
                        None,
                        "",
                        self._safe_id(asset_id),
                        f"asset has {count} manifest evidence rows",
                    )
                )
        return statuses, reasons
