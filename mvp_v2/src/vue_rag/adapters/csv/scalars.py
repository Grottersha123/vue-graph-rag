from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Final

from vue_rag.domain.schema_registry import ScalarType

_CANONICAL_INTEGER: Final = re.compile(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z")
_CANONICAL_TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"


class CsvScalarError(ValueError):
    """A scalar does not use the canonical graph CSV representation."""


def encode_scalar(value: object, scalar_type: ScalarType) -> str:
    if scalar_type is ScalarType.STRING:
        if type(value) is not str:
            raise CsvScalarError("STRING value must be str")
        return value
    if scalar_type is ScalarType.BOOLEAN:
        if type(value) is not bool:
            raise CsvScalarError("BOOLEAN value must be bool")
        return "true" if value else "false"
    if scalar_type is ScalarType.INT64:
        if type(value) is not int:
            raise CsvScalarError("INT64 value must be int")
        return str(value)
    if scalar_type is ScalarType.TIMESTAMP:
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise CsvScalarError("TIMESTAMP value must be UTC datetime")
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return _encode_vector(value)


def decode_scalar(encoded: str, scalar_type: ScalarType) -> object:
    if scalar_type is ScalarType.STRING:
        return encoded
    if scalar_type is ScalarType.BOOLEAN:
        if encoded not in {"true", "false"}:
            raise CsvScalarError("BOOLEAN must be lowercase true or false")
        return encoded == "true"
    if scalar_type is ScalarType.INT64:
        if _CANONICAL_INTEGER.fullmatch(encoded) is None:
            raise CsvScalarError("INT64 must use canonical base-10 representation")
        return int(encoded)
    if scalar_type is ScalarType.TIMESTAMP:
        return _decode_timestamp(encoded)
    return _decode_vector(encoded)


def _encode_vector(value: object) -> str:
    if not isinstance(value, tuple | list):
        raise CsvScalarError("FLOAT_VECTOR value must be an array")
    normalized: list[float] = []
    for component in value:
        if type(component) not in {int, float} or not math.isfinite(component):
            raise CsvScalarError("FLOAT_VECTOR components must be finite numbers")
        normalized.append(float(component))
    return json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _decode_timestamp(encoded: str) -> datetime:
    try:
        timestamp = datetime.strptime(encoded, _CANONICAL_TIMESTAMP_FORMAT)
    except ValueError as error:
        raise CsvScalarError(
            "TIMESTAMP must use canonical UTC microsecond Z format"
        ) from error
    canonical = timestamp.replace(tzinfo=UTC)
    if canonical.isoformat(timespec="microseconds").replace("+00:00", "Z") != encoded:
        raise CsvScalarError("TIMESTAMP must use canonical UTC microsecond Z format")
    return canonical


def _reject_json_constant(value: str) -> object:
    raise CsvScalarError(f"FLOAT_VECTOR contains invalid constant {value}")


def _decode_vector(encoded: str) -> tuple[float, ...]:
    try:
        parsed = json.loads(encoded, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, CsvScalarError) as error:
        raise CsvScalarError("FLOAT_VECTOR must be compact finite JSON") from error
    if not isinstance(parsed, list):
        raise CsvScalarError("FLOAT_VECTOR must be a JSON array")
    normalized: list[float] = []
    for component in parsed:
        if type(component) not in {int, float} or not math.isfinite(component):
            raise CsvScalarError("FLOAT_VECTOR components must be finite numbers")
        normalized.append(float(component))
    if encoded != _encode_vector(normalized):
        raise CsvScalarError("FLOAT_VECTOR must use canonical compact JSON")
    return tuple(normalized)
