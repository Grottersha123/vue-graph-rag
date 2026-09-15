from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Final, TypeVar

from loguru import logger

_LOG_FILE: Final = "app.log"
_MAX_DISPLAY_BYTES: Final = 64 * 1024
_SINK_ID: int | None = None
_SINK_PATH: Path | None = None
_LOGGER_STATE_ATTRIBUTE: Final = "_mvp_v2_file_sink"
_CONFIG_LOCK: Final = RLock()
_ResultT = TypeVar("_ResultT")


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[3] / "runtime"


def application_log_path() -> Path:
    return _runtime_root() / "logs" / _LOG_FILE


def _sink_is_active(sink_id: int) -> bool:
    core = getattr(logger, "_core", None)
    handlers = getattr(core, "handlers", {})
    return isinstance(handlers, dict) and sink_id in handlers


def configure_application_logging() -> None:
    """Configure one bounded process-local Loguru file sink."""
    global _SINK_ID, _SINK_PATH
    path = application_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve(strict=False)
    runtime = _runtime_root().resolve()
    if path.is_symlink() or not resolved.is_relative_to(runtime):
        raise RuntimeError("application log path is unsafe")
    with _CONFIG_LOCK:
        prior = getattr(logger, _LOGGER_STATE_ATTRIBUTE, None)
        if isinstance(prior, tuple) and len(prior) == 2:
            prior_id, prior_path = prior
            if (
                isinstance(prior_id, int)
                and prior_path == str(resolved)
                and _sink_is_active(prior_id)
            ):
                _SINK_ID = prior_id
                _SINK_PATH = resolved
                return
            if isinstance(prior_id, int) and _sink_is_active(prior_id):
                logger.remove(prior_id)
        sink_id = logger.add(
            resolved,
            level="INFO",
            rotation="1 MB",
            retention=3,
            encoding="utf-8",
            backtrace=True,
            diagnose=False,
            format=(
                "{time:YYYY-MM-DDTHH:mm:ssZZ} {level} {extra[operation]} "
                "{message}"
            ),
            filter=lambda record: "operation" in record["extra"],
        )
        setattr(logger, _LOGGER_STATE_ATTRIBUTE, (sink_id, str(resolved)))
        _SINK_ID = sink_id
        _SINK_PATH = resolved


@contextmanager
def logged_operation(operation: str) -> Iterator[None]:
    """Log one application operation without coupling services to Loguru."""
    operation_logger = logger.bind(operation=operation)
    started = time.monotonic()
    operation_logger.info("started")
    try:
        yield
    except Exception:
        duration_ms = round((time.monotonic() - started) * 1000)
        operation_logger.opt(exception=True).error("failed duration_ms={}", duration_ms)
        raise
    duration_ms = round((time.monotonic() - started) * 1000)
    operation_logger.info("succeeded duration_ms={}", duration_ms)


def run_logged_operation(
    operation: str, action: Callable[[], _ResultT]
) -> _ResultT:
    """Execute one operation through the shared logging boundary."""
    with logged_operation(operation):
        return action()


def record_user_action(action: str) -> None:
    """Record a UI action name without recording user-supplied values."""
    logger.bind(operation="ui_action").info("clicked action={}", action)


def record_operation_error(message: str) -> None:
    """Record an exception handled by the presentation boundary."""
    logger.bind(operation="ui").opt(exception=True).error(message)


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
