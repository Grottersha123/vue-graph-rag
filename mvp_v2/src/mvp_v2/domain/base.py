from __future__ import annotations

import hashlib
import math
import unicodedata
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, TypeAlias

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer


class ContractModel(BaseModel):
    """Strict immutable base for all validated contracts."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class AliasedContractModel(ContractModel):
    """Strict contract accepting field names and explicit aliases."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_by_name=True,
        validate_by_alias=True,
    )


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _validate_nonblank_text(value: str) -> str:
    if not value or value.isspace():
        raise ValueError("text must not be blank")
    if _has_control(value):
        raise ValueError("text must not contain Unicode control characters")
    return value


def _validate_allow_empty_text(value: str) -> str:
    if _has_control(value):
        raise ValueError("text must not contain Unicode control characters")
    return value


def _validate_multiline_controls(value: str) -> str:
    if any(
        character not in {"\n", "\t"}
        and (
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
        )
        for character in value
    ):
        raise ValueError("multiline text permits only LF and TAB control characters")
    return value


def _validate_required_multiline_text(value: str) -> str:
    _validate_multiline_controls(value)
    if not value or value.isspace():
        raise ValueError("multiline text must not be blank")
    return value


def _validate_sha256(value: str) -> str:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(
            "SHA-256 must contain exactly 64 lowercase hexadecimal characters"
        )
    return value


def _validate_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must use UTC")
    return value.astimezone(UTC)


def _serialize_utc_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_finite_vector(value: tuple[float, ...]) -> tuple[float, ...]:
    if any(
        isinstance(component, bool) or not math.isfinite(component)
        for component in value
    ):
        raise ValueError("vector components must be finite numbers and not booleans")
    return value


def _validate_relative_path(value: str) -> str:
    _validate_nonblank_text(value)
    if "\\" in value:
        raise ValueError("path must use POSIX separators")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("path must be lexical, relative, and contain no dot segments")
    return value


def _validate_optional_relative_path(value: str) -> str:
    if value == "":
        return value
    return _validate_relative_path(value)


NonBlankText: TypeAlias = Annotated[
    str, Field(strict=True), AfterValidator(_validate_nonblank_text)
]
AllowEmptyText: TypeAlias = Annotated[
    str, Field(strict=True), AfterValidator(_validate_allow_empty_text)
]
RequiredMultilineText: TypeAlias = Annotated[
    str, Field(strict=True), AfterValidator(_validate_required_multiline_text)
]
AllowEmptyMultilineText: TypeAlias = Annotated[
    str, Field(strict=True), AfterValidator(_validate_multiline_controls)
]
EntityId: TypeAlias = NonBlankText
Sha256: TypeAlias = Annotated[str, Field(strict=True), AfterValidator(_validate_sha256)]
UtcDateTime: TypeAlias = Annotated[
    datetime,
    Field(strict=True),
    AfterValidator(_validate_utc_datetime),
    PlainSerializer(_serialize_utc_datetime, return_type=str, when_used="json"),
]
FiniteVector: TypeAlias = Annotated[
    tuple[float, ...], Field(strict=True), AfterValidator(_validate_finite_vector)
]
RelativePath: TypeAlias = Annotated[
    str, Field(strict=True), AfterValidator(_validate_relative_path)
]
OptionalRelativePath: TypeAlias = Annotated[
    str, Field(strict=True), AfterValidator(_validate_optional_relative_path)
]


def stable_id(prefix: str, *parts: str) -> str:
    """Create an ID with behavior identical to the legacy implementation."""
    if not prefix or any(not isinstance(part, str) for part in parts):
        raise ValueError("stable IDs require a kind and string parts")
    payload = "\x1f".join((prefix, *parts)).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:24]
    return f"{prefix}-{digest}"
