from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

_LOGGER_NAME: Final = "mvp_v2"
_LOG_FILE: Final = "app.log"
_MAX_DISPLAY_BYTES: Final = 64 * 1024


def application_log_path() -> Path:
    return Path(__file__).resolve().parents[3] / "runtime" / "logs" / _LOG_FILE


def configure_application_logging() -> logging.Logger:
    """Configure one bounded file handler and one console handler per process."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    path = application_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve(strict=False)
    runtime = (Path(__file__).resolve().parents[3] / "runtime").resolve()
    if path.is_symlink() or not resolved.is_relative_to(runtime):
        raise RuntimeError("application log path is unsafe")
    file_name = str(resolved)
    if not any(
        isinstance(handler, RotatingFileHandler) and handler.baseFilename == file_name
        for handler in logger.handlers
    ):
        file_handler = RotatingFileHandler(
            file_name,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        logger.addHandler(file_handler)
    if not any(type(handler) is logging.StreamHandler for handler in logger.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(console)
    return logger


def read_log_tail() -> str:
    """Read a bounded UTF-8 tail without following a symlink."""
    path = application_log_path()
    if path.is_symlink() or not path.is_file():
        return ""
    with path.open("rb") as stream:
        size = path.stat().st_size
        stream.seek(max(0, size - _MAX_DISPLAY_BYTES))
        raw = stream.read(_MAX_DISPLAY_BYTES)
    return raw.decode("utf-8", errors="replace")
