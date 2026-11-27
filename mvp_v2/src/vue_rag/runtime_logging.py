from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Final

from vue_rag.backend.config import LogLevel
from vue_rag.redaction import redact_text, register_secret_values

_LOG_FORMAT: Final = (
    "%(asctime)s level=%(levelname)s component=%(name)s message=%(message)s"
)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = redact_text(record.getMessage())
        if record.exc_info is not None and record.exc_info[0] is not None:
            exception_type = record.exc_info[0].__name__
            message = f"{message} exception_type={exception_type}"
        record.msg = message
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def configure_runtime_logging(
    level: LogLevel,
    *,
    component: str,
    secrets: Iterable[str | None] = (),
) -> logging.Logger:
    register_secret_values(secrets)
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level))
    logging.captureWarnings(True)
    logger = logging.getLogger(component)
    logger.debug("runtime logging configured level=%s", level)
    return logger
