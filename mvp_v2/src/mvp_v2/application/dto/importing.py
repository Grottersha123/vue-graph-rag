from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from mvp_v2.domain.base import (
    AllowEmptyText,
    ContractModel,
    NonBlankText,
    RelativePath,
    Sha256,
)
from mvp_v2.domain.snapshot import GraphSnapshot

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]


class ImportIssue(ContractModel):
    severity: Literal["error", "warning", "info"]
    code: NonBlankText
    category: NonBlankText
    source_table: NonBlankText
    row: PositiveInt | None = None
    column: AllowEmptyText = ""
    record_id: AllowEmptyText = ""
    message: NonBlankText


class SourceCsvFact(ContractModel):
    table: NonBlankText
    sha256: Sha256
    data_rows: NonNegativeInt


class NamedCount(ContractModel):
    name: NonBlankText
    count: NonNegativeInt


class FieldDisposition(ContractModel):
    table: NonBlankText
    field: NonBlankText
    disposition: NonBlankText
    count: NonNegativeInt


class CopyPlanEntry(ContractModel):
    source_path: RelativePath
    target_path: RelativePath
    size: NonNegativeInt
    sha256: Sha256


class MediaCopyPlan(ContractModel):
    entries: tuple[CopyPlanEntry, ...] = ()
    total_files: NonNegativeInt
    total_bytes: NonNegativeInt

    @model_validator(mode="after")
    def validate_totals(self) -> MediaCopyPlan:
        if self.total_files != len(self.entries):
            raise ValueError("total_files does not match entries")
        if self.total_bytes != sum(entry.size for entry in self.entries):
            raise ValueError("total_bytes does not match entries")
        paths = tuple(entry.target_path for entry in self.entries)
        if len(paths) != len(set(paths)):
            raise ValueError("copy-plan target paths must be unique")
        return self


class ImportReport(ContractModel):
    report_id: Sha256
    snapshot_sha256: Sha256
    copy_plan_sha256: Sha256
    source_csvs: tuple[SourceCsvFact, ...]
    target_counts: tuple[NamedCount, ...]
    transformations: tuple[NamedCount, ...]
    dropped_fields: tuple[FieldDisposition, ...]
    link_counts: tuple[NamedCount, ...]
    legacy_error_counts: tuple[NamedCount, ...]
    validation_counts: tuple[NamedCount, ...]
    manifest_status_counts: tuple[NamedCount, ...]
    manifest_reason_counts: tuple[NamedCount, ...]
    documents_without_process: tuple[str, ...]
    media_plan_files: NonNegativeInt
    media_plan_bytes: NonNegativeInt
    issues: tuple[ImportIssue, ...]
    is_publishable: bool

    @model_validator(mode="after")
    def validate_identity(self) -> ImportReport:
        expected = compute_report_id(
            self.model_dump(mode="json", exclude={"report_id"})
        )
        if self.report_id != expected:
            raise ValueError("report_id does not match canonical report facts")
        if self.is_publishable == any(
            issue.severity == "error" for issue in self.issues
        ):
            raise ValueError("is_publishable does not match issue severities")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class LegacyImportResult(ContractModel):
    snapshot: GraphSnapshot
    report: ImportReport
    copy_plan: MediaCopyPlan

    @model_validator(mode="after")
    def validate_approved_content_bindings(self) -> LegacyImportResult:
        if self.report.snapshot_sha256 != compute_snapshot_sha256(self.snapshot):
            raise ValueError("report does not bind the exact graph snapshot")
        if self.report.copy_plan_sha256 != compute_copy_plan_sha256(self.copy_plan):
            raise ValueError("report does not bind the exact media copy plan")
        return self


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def compute_snapshot_sha256(snapshot: GraphSnapshot) -> str:
    return _canonical_sha256(snapshot.model_dump(mode="json", by_alias=True))


def compute_copy_plan_sha256(copy_plan: MediaCopyPlan) -> str:
    return _canonical_sha256(copy_plan.model_dump(mode="json", by_alias=True))


def compute_report_id(facts: object) -> str:
    return _canonical_sha256(facts)
