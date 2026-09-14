from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from mvp_v2.application.dto.importing import ImportIssue

ModelT = TypeVar("ModelT", bound=BaseModel)
ERROR_MIGRATION_MESSAGE = (
    "Legacy HAS_ERROR marker; no legacy version error text was available."
)


class LegacyScalarError(ValueError):
    def __init__(self, column: str) -> None:
        super().__init__("invalid legacy scalar")
        self.column = column


class LegacyTools:
    @staticmethod
    def _bool(value: str, column: str) -> bool:
        if value == "true":
            return True
        if value == "false":
            return False
        raise LegacyScalarError(column)

    @staticmethod
    def _positive_int(value: str, column: str) -> int:
        if not value.isascii() or not value.isdigit() or value.startswith("0"):
            raise LegacyScalarError(column)
        result = int(value)
        if result < 1:
            raise LegacyScalarError(column)
        return result

    @staticmethod
    def _nonnegative_int(value: str, column: str) -> int:
        if (
            not value.isascii()
            or not value.isdigit()
            or (len(value) > 1 and value.startswith("0"))
        ):
            raise LegacyScalarError(column)
        return int(value)

    @staticmethod
    def _utc_datetime(value: str, column: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        offset = parsed.utcoffset()
        if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise LegacyScalarError(column)
        return parsed

    @staticmethod
    def _rewrite_path(value: str, prefix: str, replacement: str) -> str:
        if not value.startswith(prefix):
            raise LegacyScalarError("local_path")
        suffix = value[len(prefix) :]
        path = PurePosixPath(suffix)
        if (
            not suffix
            or "\\" in suffix
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in suffix.split("/"))
        ):
            raise LegacyScalarError("local_path")
        return replacement + suffix

    @staticmethod
    def _duplicate_ids(
        table: str, values: tuple[ModelT, ...] | list[ModelT], issues: list[ImportIssue]
    ) -> None:
        counts = Counter(str(item.model_dump().get("id", "")) for item in values)
        for identifier, count in sorted(counts.items()):
            if count > 1:
                issues.append(
                    LegacyTools._issue(
                        "error",
                        "DUPLICATE_ID",
                        "duplicate",
                        table,
                        None,
                        "id",
                        LegacyTools._safe_id(identifier),
                        f"ID occurs {count} times",
                    )
                )

    @staticmethod
    def _validation_column(error: ValidationError) -> str:
        location = error.errors()[0].get("loc", ())
        return str(location[0]) if location else ""

    @staticmethod
    def _error_column(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return LegacyTools._validation_column(error)
        if isinstance(error, LegacyScalarError):
            return error.column
        return ""

    @staticmethod
    def _safe_id(value: str) -> str:
        if (
            len(value) <= 160
            and value
            and all(ord(char) >= 32 and char not in "\r\n" for char in value)
        ):
            return value
        return (
            "sha256:"
            + hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
        )

    @staticmethod
    def _safe_category(value: str) -> str:
        if (
            value
            and len(value) <= 100
            and all(ord(character) >= 32 for character in value)
        ):
            return value
        if not value:
            return "<empty>"
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        return f"unsafe:{digest[:16]}"

    @staticmethod
    def _safe_edge(source: str, target: str) -> str:
        return f"{LegacyTools._safe_id(source)}->{LegacyTools._safe_id(target)}"

    @staticmethod
    def _issue(
        severity: Literal["error", "warning", "info"],
        code: str,
        category: str,
        table: str,
        row: int | None,
        column: str,
        record_id: str,
        message: str,
    ) -> ImportIssue:
        return ImportIssue(
            severity=severity,
            code=code,
            category=category,
            source_table=table,
            row=row,
            column=column,
            record_id=record_id,
            message=message,
        )
