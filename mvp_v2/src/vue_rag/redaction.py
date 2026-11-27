from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from typing import Final

_NAMED_SECRET: Final = re.compile(
    r"(?i)\b(authorization|api[_-]?key|token|password)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_SECRET: Final = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_LOCK: Final = threading.Lock()
_SECRET_VALUES: tuple[str, ...] = ()


def register_secret_values(values: Iterable[str | None]) -> None:
    global _SECRET_VALUES
    additions = {value for value in values if value is not None and len(value) >= 4}
    if not additions:
        return
    with _SECRET_LOCK:
        _SECRET_VALUES = tuple(
            sorted(set(_SECRET_VALUES) | additions, key=len, reverse=True)
        )


def redact_text(value: str) -> str:
    redacted = value
    with _SECRET_LOCK:
        secrets = _SECRET_VALUES
    for secret in secrets:
        redacted = redacted.replace(secret, "<redacted>")
    redacted = _BEARER_SECRET.sub("Bearer <redacted>", redacted)
    return _NAMED_SECRET.sub(r"\1\2<redacted>", redacted)
