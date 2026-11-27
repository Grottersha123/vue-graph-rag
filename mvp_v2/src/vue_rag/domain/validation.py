from __future__ import annotations

from typing import Literal

from vue_rag.domain.base import AllowEmptyText, ContractModel, NonBlankText


class ValidationIssue(ContractModel):
    severity: Literal["error", "warning"]
    code: NonBlankText
    table: NonBlankText
    record_id: AllowEmptyText = ""
    message: NonBlankText


class ValidationResult(ContractModel):
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")


def build_validation_result(issues: list[ValidationIssue]) -> ValidationResult:
    ordered = sorted(
        issues,
        key=lambda issue: (
            issue.severity,
            issue.code,
            issue.table,
            issue.record_id,
            issue.message,
        ),
    )
    return ValidationResult(issues=tuple(ordered))
