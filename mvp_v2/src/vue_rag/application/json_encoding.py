from __future__ import annotations

import json


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON value with stable ordering and a trailing newline."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
