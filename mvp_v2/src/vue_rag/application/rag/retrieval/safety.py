from __future__ import annotations

import re
from typing import Final

_PHONE_NUMBER: Final = re.compile(
    r"(?:"
    r"(?<!\w)\+?\d(?:[\s().-]*\d){9,14}(?!\w)"
    r"|(?<!\w)\d{3}[ -]\d{2}[ -]\d{2}(?!\w)"
    r")"
)


def contains_phone_number(text: str) -> bool:
    return _PHONE_NUMBER.search(text) is not None
