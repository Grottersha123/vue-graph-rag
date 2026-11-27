from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

ExtensionScalarType: TypeAlias = Literal["STRING", "INT64", "BOOLEAN", "FLOAT_VECTOR"]
ExtensionDefault: TypeAlias = str | int | bool | tuple[float, ...]
_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


class FieldExtensionError(ValueError):
    """The user-editable graph field declaration is invalid."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtensionField:
    name: str
    scalar_type: ExtensionScalarType
    default: ExtensionDefault


def load_field_extensions(
    path: Path | None = None,
) -> dict[str, tuple[ExtensionField, ...]]:
    source = path or Path(__file__).parent.parent / "graph_fields.json"
    try:
        payload: object = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FieldExtensionError("graph field config could not be read") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "nodes"}
        or payload.get("version") != 1
    ):
        raise FieldExtensionError("graph field config version or keys are invalid")
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict):
        raise FieldExtensionError("graph field config nodes must be an object")
    parsed: dict[str, tuple[ExtensionField, ...]] = {}
    for node_name, declarations in nodes.items():
        if not isinstance(node_name, str) or not isinstance(declarations, list):
            raise FieldExtensionError("node field declarations are invalid")
        fields = tuple(_parse_field(item) for item in declarations)
        names = tuple(item.name for item in fields)
        if len(names) != len(set(names)):
            raise FieldExtensionError(f"duplicate extension field in {node_name}")
        parsed[node_name] = fields
    return parsed


def _parse_field(value: object) -> ExtensionField:
    if not isinstance(value, dict) or set(value) != {"name", "type", "default"}:
        raise FieldExtensionError("extension fields require name, type, and default")
    name = value["name"]
    scalar_type = value["type"]
    if not isinstance(name, str) or _FIELD_NAME.fullmatch(name) is None:
        raise FieldExtensionError("extension field name is invalid")
    if scalar_type not in {"STRING", "INT64", "BOOLEAN", "FLOAT_VECTOR"}:
        raise FieldExtensionError(f"unsupported extension type for {name}")
    default = _parse_default(name, scalar_type, value["default"])
    return ExtensionField(name=name, scalar_type=scalar_type, default=default)


def _parse_default(name: str, scalar_type: object, value: object) -> ExtensionDefault:
    if scalar_type == "STRING" and isinstance(value, str):
        return value
    if scalar_type == "INT64" and type(value) is int:
        return value
    if scalar_type == "BOOLEAN" and type(value) is bool:
        return value
    if scalar_type == "FLOAT_VECTOR" and isinstance(value, list):
        if any(
            type(component) not in {int, float} or not math.isfinite(float(component))
            for component in value
        ):
            raise FieldExtensionError(f"invalid FLOAT_VECTOR default for {name}")
        return tuple(float(component) for component in value)
    raise FieldExtensionError(f"default does not match type for {name}")
