from __future__ import annotations

from collections import Counter, defaultdict
from itertools import pairwise
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from mvp_v2.adapters.legacy.common import ERROR_MIGRATION_MESSAGE, LegacyTools
from mvp_v2.adapters.legacy.reader import LegacyTable
from mvp_v2.application.dto.importing import ImportIssue
from mvp_v2.domain import graph

ModelT = TypeVar("ModelT", bound=BaseModel)


class LegacyParser(LegacyTools):
    def _models(
        self, table: LegacyTable, model: type[ModelT], issues: list[ImportIssue]
    ) -> tuple[ModelT, ...]:
        values: list[ModelT] = []
        for row_number, row in table.rows:
            try:
                values.append(model.model_validate(row))
            except ValidationError as error:
                column = self._validation_column(error)
                issues.append(
                    self._issue(
                        "error",
                        "MODEL_ERROR",
                        "model",
                        table.name,
                        row_number,
                        column,
                        self._safe_id(row.get("id", "")),
                        "row does not satisfy the target model",
                    )
                )
        self._duplicate_ids(table.name, values, issues)
        return tuple(values)

    def _documents(
        self, table: LegacyTable, issues: list[ImportIssue]
    ) -> tuple[graph.Document, ...]:
        fields = (
            "id",
            "title",
            "source_url",
            "external_id",
            "content_format",
            "source_name",
        )
        values: list[graph.Document] = []
        for number, row in table.rows:
            try:
                payload: dict[str, object] = {field: row[field] for field in fields}
                payload["is_active"] = self._bool(row["is_active"], "is_active")
                values.append(graph.Document.model_validate(payload))
            except (ValueError, ValidationError, KeyError) as error:
                issues.append(
                    self._issue(
                        "error",
                        "SCALAR_OR_MODEL_ERROR",
                        "model",
                        table.name,
                        number,
                        self._error_column(error),
                        self._safe_id(row.get("id", "")),
                        "row contains an invalid scalar or target field",
                    )
                )
        self._duplicate_ids(table.name, values, issues)
        return tuple(values)

    def _versions(
        self,
        table: LegacyTable,
        owners: dict[str, str],
        error_docs: set[str],
        issues: list[ImportIssue],
    ) -> tuple[tuple[graph.DocumentVersion, ...], int]:
        values: list[graph.DocumentVersion] = []
        error_rules = 0
        for number, row in table.rows:
            try:
                error_text = row["error"]
                if (
                    not row["sha256"]
                    and not error_text
                    and owners.get(row["id"]) in error_docs
                ):
                    error_text = ERROR_MIGRATION_MESSAGE
                    error_rules += 1
                    issues.append(
                        self._issue(
                            "warning",
                            "ERROR_TEXT_MIGRATION_RULE",
                            "transformation",
                            table.name,
                            number,
                            "error",
                            self._safe_id(row["id"]),
                            (
                                "empty error text replaced by the fixed HAS_ERROR "
                                "migration diagnostic"
                            ),
                        )
                    )
                payload: dict[str, object] = dict(row)
                payload["version"] = self._positive_int(row["version"], "version")
                payload["created_at"] = self._utc_datetime(
                    row["created_at"], "created_at"
                )
                is_current = self._bool(row["is_current"], "is_current")
                if not row["sha256"] and is_current:
                    is_current = False
                    issues.append(
                        self._issue(
                            "warning",
                            "CURRENT_FLAG_REWRITTEN_FOR_EMPTY_SHA",
                            "transformation",
                            table.name,
                            number,
                            "is_current",
                            self._safe_id(row["id"]),
                            (
                                "empty-SHA errored version was made non-current by "
                                "explicit migration rule"
                            ),
                        )
                    )
                payload["is_current"] = is_current
                payload["local_path"] = (
                    self._rewrite_path(
                        row["local_path"], "mvp/mvp_data/documents/", "documents/"
                    )
                    if row["local_path"]
                    else ""
                )
                payload["error"] = error_text
                values.append(graph.DocumentVersion.model_validate(payload))
            except (ValueError, ValidationError) as error:
                issues.append(
                    self._issue(
                        "error",
                        "SCALAR_OR_MODEL_ERROR",
                        "model",
                        table.name,
                        number,
                        self._error_column(error),
                        self._safe_id(row.get("id", "")),
                        "row contains an invalid scalar, path, or target field",
                    )
                )
        self._duplicate_ids(table.name, values, issues)
        return tuple(values), error_rules

    def _assets(
        self, table: LegacyTable, issues: list[ImportIssue]
    ) -> tuple[graph.Asset, ...]:
        values: list[graph.Asset] = []
        for number, row in table.rows:
            if row.get("asset_type") != "image":
                issues.append(
                    self._issue(
                        "error",
                        "INCOMPATIBLE_ASSET_TYPE",
                        "model",
                        table.name,
                        number,
                        "asset_type",
                        self._safe_id(row.get("id", "")),
                        "legacy asset type is incompatible with target image type",
                    )
                )
                continue
            try:
                payload: dict[str, object] = dict(row)
                payload["asset_type"] = "image"
                payload["position"] = self._nonnegative_int(row["position"], "position")
                payload["local_path"] = self._rewrite_path(
                    row["local_path"], "mvp/mvp_data/images/", "images/"
                )
                payload["llm_description"] = ""
                values.append(graph.Asset.model_validate(payload))
            except (ValueError, ValidationError) as error:
                issues.append(
                    self._issue(
                        "error",
                        "SCALAR_OR_MODEL_ERROR",
                        "model",
                        table.name,
                        number,
                        self._error_column(error),
                        self._safe_id(row.get("id", "")),
                        "row contains an invalid scalar, path, or target field",
                    )
                )
        self._duplicate_ids(table.name, values, issues)
        return tuple(values)

    def _relations(
        self, table: LegacyTable, model: type[ModelT], issues: list[ImportIssue]
    ) -> tuple[ModelT, ...]:
        values: list[ModelT] = []
        seen: Counter[tuple[str, str]] = Counter()
        for number, row in table.rows:
            try:
                values.append(
                    model.model_validate({"from": row["from"], "to": row["to"]})
                )
                seen[(row["from"], row["to"])] += 1
            except ValidationError:
                issues.append(
                    self._issue(
                        "error",
                        "MODEL_ERROR",
                        "model",
                        table.name,
                        number,
                        "",
                        self._safe_edge(row.get("from", ""), row.get("to", "")),
                        "relation row does not satisfy the target model",
                    )
                )
        for edge, count in sorted(seen.items()):
            if count > 1:
                issues.append(
                    self._issue(
                        "error",
                        "DUPLICATE_EDGE",
                        "duplicate",
                        table.name,
                        None,
                        "",
                        self._safe_edge(*edge),
                        f"typed edge occurs {count} times",
                    )
                )
        return tuple(values)

    def _reverse_source_edges(
        self, table: LegacyTable, issues: list[ImportIssue]
    ) -> tuple[graph.HasSourceType, ...]:
        raw = self._relations(table, graph.HasSourceType, issues)
        return tuple(
            graph.HasSourceType(source_id=item.target_id, target_id=item.source_id)
            for item in raw
        )

    def _build_supersedes(
        self,
        versions: tuple[graph.DocumentVersion, ...],
        owners: tuple[graph.HasVersion, ...],
        issues: list[ImportIssue],
    ) -> tuple[graph.Supersedes, ...]:
        by_id = {item.id: item for item in versions}
        grouped: dict[str, list[graph.DocumentVersion]] = defaultdict(list)
        for edge in owners:
            if edge.target_id in by_id:
                grouped[edge.source_id].append(by_id[edge.target_id])
        result: list[graph.Supersedes] = []
        for document_id, items in sorted(grouped.items()):
            counts = Counter(item.version for item in items)
            for version_number, count in sorted(counts.items()):
                if count > 1:
                    issues.append(
                        self._issue(
                            "error",
                            "DUPLICATE_VERSION_NUMBER",
                            "cardinality",
                            "DocumentVersion",
                            None,
                            "version",
                            self._safe_id(document_id),
                            f"version {version_number} occurs {count} times",
                        )
                    )
            ordered = sorted(items, key=lambda item: (item.version, item.id))
            for previous, current in pairwise(ordered):
                result.append(
                    graph.Supersedes(source_id=current.id, target_id=previous.id)
                )
        return tuple(result)
