from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from mvp_v2.application.dto.importing import (
    FieldDisposition,
    ImportIssue,
    ImportReport,
    NamedCount,
    SourceCsvFact,
    compute_report_id,
    compute_snapshot_sha256,
)
from mvp_v2.domain.snapshot import GraphSnapshot


def build_import_report(
    *,
    source_facts: tuple[SourceCsvFact, ...],
    snapshot: GraphSnapshot,
    issues: list[ImportIssue],
    source_file_count: int,
    source_file_bytes: int,
    source_tree_sha256: str,
    error_rules: int,
    legacy_error_rows: int,
    manual_resolved: int,
    manual_unresolved: int,
    manifest_status: Counter[str],
    manifest_reason: Counter[str],
    documents_without_process: tuple[str, ...],
) -> ImportReport:
    ordered_issues = tuple(
        sorted(
            issues,
            key=lambda x: (
                x.severity,
                x.code,
                x.category,
                x.source_table,
                x.row or 0,
                x.column,
                x.record_id,
                x.message,
            ),
        )
    )
    target_counts = tuple(
        NamedCount(name=name, count=len(snapshot.__getattribute__(field)))
        for name, field in (
            ("SourceType", "source_types"),
            ("Document", "documents"),
            ("DocumentVersion", "document_versions"),
            ("Chunk", "chunks"),
            ("Asset", "assets"),
            ("Embedding", "embeddings"),
            ("Role", "roles"),
            ("Process", "processes"),
            ("Status", "statuses"),
            ("HAS_SOURCE_TYPE", "has_source_types"),
            ("HAS_VERSION", "has_versions"),
            ("SUPERSEDES", "supersedes"),
            ("HAS_STATUS", "has_statuses"),
            ("HAS_CHUNK", "has_chunks"),
            ("HAS_ASSET", "has_assets"),
            ("CONTAINS_ASSET", "contains_assets"),
            ("HAS_EMBEDDING_CHUNK", "has_embedding_chunks"),
            ("HAS_EMBEDDING_ASSET", "has_embedding_assets"),
            ("LINKS_TO", "links_to"),
            ("DESCRIBES", "describes"),
            ("FOR_ROLE", "for_roles"),
        )
    )
    dispositions = tuple(
        FieldDisposition(
            table="Document",
            field=field,
            disposition=disposition,
            count=len(snapshot.documents),
        )
        for field, disposition in (
            ("sha256", "owned by current DocumentVersion; cross-checked"),
            ("local_path", "owned by current DocumentVersion; cross-checked"),
            (
                "status",
                "owned by current DocumentVersion HAS_STATUS; cross-checked",
            ),
            ("source_type", "owned by reversed HAS_DOCUMENT edge; cross-checked"),
            (
                "source_format",
                "not represented in target schema; dropped and reported",
            ),
        )
    )
    facts = dict(
        snapshot_sha256=compute_snapshot_sha256(snapshot),
        source_csvs=tuple(sorted(source_facts, key=lambda x: x.table)),
        target_counts=target_counts,
        transformations=(
            NamedCount(
                name="HAS_DOCUMENT reversed to HAS_SOURCE_TYPE",
                count=len(snapshot.has_source_types),
            ),
            NamedCount(name="SUPERSEDES constructed", count=len(snapshot.supersedes)),
            NamedCount(name="fixed HAS_ERROR text rule", count=error_rules),
            NamedCount(
                name="CURRENT_FLAG_REWRITTEN_FOR_EMPTY_SHA",
                count=sum(
                    issue.code == "CURRENT_FLAG_REWRITTEN_FOR_EMPTY_SHA"
                    for issue in ordered_issues
                ),
            ),
            NamedCount(
                name="TIMESTAMP_CANONICALIZED",
                count=len(snapshot.document_versions),
            ),
            NamedCount(
                name="PATH_REWRITTEN_TO_AUTHORITY",
                count=sum(bool(item.local_path) for item in snapshot.document_versions)
                + len(snapshot.assets),
            ),
            NamedCount(
                name="Asset llm_description initialized empty",
                count=len(snapshot.assets),
            ),
        ),
        dropped_fields=dispositions,
        link_counts=(
            NamedCount(name="manual_resolved", count=manual_resolved),
            NamedCount(name="manual_unresolved", count=manual_unresolved),
            NamedCount(name="target_links", count=len(snapshot.links_to)),
        ),
        legacy_error_counts=(
            NamedCount(name="HAS_ERROR_rows", count=legacy_error_rows),
            NamedCount(name="fixed_error_text", count=error_rules),
        ),
        validation_counts=(
            NamedCount(
                name="errors",
                count=sum(1 for issue in ordered_issues if issue.severity == "error"),
            ),
            NamedCount(
                name="warnings",
                count=sum(1 for issue in ordered_issues if issue.severity == "warning"),
            ),
        ),
        manifest_status_counts=tuple(
            NamedCount(name=name, count=count)
            for name, count in sorted(manifest_status.items())
        ),
        manifest_reason_counts=tuple(
            NamedCount(name=name, count=count)
            for name, count in sorted(manifest_reason.items())
        ),
        documents_without_process=documents_without_process,
        source_file_count=source_file_count,
        source_file_bytes=source_file_bytes,
        source_tree_sha256=source_tree_sha256,
        issues=ordered_issues,
        is_publishable=not any(issue.severity == "error" for issue in ordered_issues),
    )
    report_id = compute_report_id({key: _json(value) for key, value in facts.items()})
    return ImportReport.model_validate({"report_id": report_id, **facts})


def _json(value: object) -> object:
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
