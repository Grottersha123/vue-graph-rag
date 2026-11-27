from __future__ import annotations

import re
from typing import Final

REDACTED: Final = "[redacted]"

_PATTERNS: Final = (
    re.compile(
        r"\bhttps?://[^\s)]*(?:token|key|secret|password|signature)=[^\s)]*",
        re.I,
    ),
    re.compile(
        r"\b(?:api[_-]?key|token|токен|secret|password|passwd|пароль|otp|code|код)"
        r"\s*[:=]\s*\S+",
        re.I,
    ),
    re.compile(r"\b\d{6,8}\b(?=\s*(?:otp|код|code))", re.I),
    re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,32}\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)"),
    re.compile(r"\b(?:chat|user|channel|message)[_-]?id\s*[:=]?\s*-?\d+", re.I),
    re.compile(r"\b(?:tg|telegram)[_-]?id\s*[:=]?\s*-?\d+", re.I),
)


def redact(text: str) -> str:
    """Remove personal data, secrets, credential links, and chat identifiers."""

    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result


def contains_sensitive(text: str) -> bool:
    """Return True when redaction would change the text."""

    return redact(text) != text


def is_pii_redacted(text: str) -> bool:
    """Return True when no sensitive pattern remains in the text."""

    return not contains_sensitive(text)
