from __future__ import annotations

import contextvars
import fcntl
import hashlib
import os
import threading
import traceback as traceback_module
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Protocol
from uuid import uuid4

from pydantic import Field, ValidationError

from mvp_v2.domain.base import ContractModel, UtcDateTime

SCHEMA_VERSION: Final = 1
MAX_ERROR_CHARS: Final = 2_048
MAX_TRACEBACK_CHARS: Final = 32_768


class AuditWriteError(RuntimeError):
    pass


class EventEnvelope(ContractModel):
    schema_version: Literal[1]
    event_id: str
    event_ts: UtcDateTime
    component: Literal["backend", "telegram"]
    event_type: str
    request_id: str | None
    conversation_id: str | None
    status: str


class BackendEvent(EventEnvelope):
    component: Literal["backend"]
    request_id: str
    conversation_id: str
    status: Literal["success", "error", "rejected", "overloaded"]
    telegram_user_id: int
    question: str
    answer: str | None
    profile_id: str | None
    prompt_version: str | None
    database_name: str
    database_build_id: str
    graph_schema_version: str
    model_id: str
    latency_ms: Annotated[int, Field(strict=True, ge=0)]
    retrieval_count: Annotated[int, Field(strict=True, ge=0)]
    evidence_ids: tuple[str, ...]


class TelegramEvent(EventEnvelope):
    component: Literal["telegram"]
    request_id: str
    conversation_id: str
    status: Literal["received", "sent", "failed", "skipped"]
    user_id: int
    chat_id: int
    source_message_id: int | None
    action: str
    delivery_status: str
    related_request_id: str | None = None


class ErrorEvent(EventEnvelope):
    status: Literal["error"]
    operation: str
    error_code: str
    message: str
    error: Annotated[str, Field(max_length=MAX_ERROR_CHARS)] | None = None
    traceback: Annotated[str, Field(max_length=MAX_TRACEBACK_CHARS)] | None = None


def exception_diagnostics(
    exception: BaseException | None,
) -> tuple[str | None, str | None]:
    if exception is None:
        return None, None
    error = f"{type(exception).__name__}: {exception}"[:MAX_ERROR_CHARS]
    formatted = "".join(
        traceback_module.format_exception(
            type(exception), exception, exception.__traceback__
        )
    )
    return error, formatted[-MAX_TRACEBACK_CHARS:]


class PromptTraceEvent(EventEnvelope):
    component: Literal["backend"]
    request_id: str
    conversation_id: str
    status: Literal["captured", "failed"]
    attempt: Annotated[int, Field(strict=True, ge=1)]
    profile_id: str
    prompt_version: str
    database_name: str
    database_build_id: str
    graph_schema_version: str
    prompt_hash: str
    rendered_prompt: str


class FeedbackEvent(EventEnvelope):
    component: Literal["backend"]
    request_id: str
    conversation_id: str
    status: Literal["accepted", "error"]
    telegram_user_id: int
    rating: Annotated[int, Field(strict=True, ge=1, le=5)]
    comment: str | None


def event_envelope(
    *,
    component: Literal["backend", "telegram"],
    event_type: str,
    request_id: str | None,
    conversation_id: str | None,
    status: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": uuid4().hex,
        "event_ts": datetime.now(UTC),
        "component": component,
        "event_type": event_type,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "status": status,
    }


class JsonlAuditWriter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def prepare(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._path.parent.is_symlink() or not self._path.parent.is_dir():
            raise AuditWriteError("audit directory is invalid")
        if self._path.exists() and (
            self._path.is_symlink() or not self._path.is_file()
        ):
            raise AuditWriteError("audit target is invalid")
        if not self._path.exists():
            descriptor = os.open(
                self._path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)

    def ready(self) -> bool:
        target = self._path if self._path.exists() else self._path.parent
        return (
            self._path.parent.is_dir()
            and not self._path.parent.is_symlink()
            and not self._path.is_symlink()
            and os.access(target, os.W_OK)
        )

    def append(self, record: ContractModel) -> None:
        encoded = (record.model_dump_json() + "\n").encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            with self._lock:
                descriptor = os.open(self._path, flags, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    remaining = memoryview(encoded)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written < 1:
                            raise OSError("audit append made no progress")
                        remaining = remaining[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError as error:
            raise AuditWriteError("audit append failed") from error


class CompletedRequestRegistry:
    def __init__(self, maximum_entries: int = 10_000) -> None:
        self._entries: OrderedDict[str, tuple[int, str]] = OrderedDict()
        self._maximum_entries = maximum_entries
        self._lock = threading.Lock()

    def add(self, request_id: str, telegram_user_id: int, conversation_id: str) -> None:
        with self._lock:
            self._entries[request_id] = (telegram_user_id, conversation_id)
            self._entries.move_to_end(request_id)
            while len(self._entries) > self._maximum_entries:
                self._entries.popitem(last=False)

    def claim(
        self, request_id: str, telegram_user_id: int, conversation_id: str
    ) -> bool:
        expected = (telegram_user_id, conversation_id)
        with self._lock:
            if self._entries.get(request_id) != expected:
                return False
            del self._entries[request_id]
            return True

    def rebuild(self, backend_path: Path, feedback_path: Path) -> None:
        with self._lock:
            self._entries.clear()
        try:
            with backend_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        backend_event = BackendEvent.model_validate_json(line)
                    except ValidationError:
                        continue
                    if (
                        backend_event.status == "success"
                        and backend_event.request_id is not None
                        and backend_event.conversation_id is not None
                    ):
                        self.add(
                            backend_event.request_id,
                            backend_event.telegram_user_id,
                            backend_event.conversation_id,
                        )
            with feedback_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        feedback_event = FeedbackEvent.model_validate_json(line)
                    except ValidationError:
                        continue
                    if (
                        feedback_event.request_id is not None
                        and feedback_event.conversation_id is not None
                    ):
                        self.claim(
                            feedback_event.request_id,
                            feedback_event.telegram_user_id,
                            feedback_event.conversation_id,
                        )
        except OSError as error:
            raise AuditWriteError("audit correlation index rebuild failed") from error


class GatewayAudit:
    def __init__(self, directory: Path) -> None:
        self.backend_writer = JsonlAuditWriter(directory / "backend.jsonl")
        self.error_writer = JsonlAuditWriter(directory / "errors.jsonl")
        self.prompt_writer = JsonlAuditWriter(directory / "prompt_traces.jsonl")
        self.feedback_writer = JsonlAuditWriter(directory / "feedback.jsonl")
        self.completed = CompletedRequestRegistry()

    def prepare(self) -> None:
        for writer in (
            self.backend_writer,
            self.error_writer,
            self.prompt_writer,
            self.feedback_writer,
        ):
            writer.prepare()
        self.completed.rebuild(
            self.backend_writer.path,
            self.feedback_writer.path,
        )

    def ready(self) -> bool:
        return all(
            writer.ready()
            for writer in (
                self.backend_writer,
                self.error_writer,
                self.prompt_writer,
                self.feedback_writer,
            )
        )


class TelegramAudit:
    def __init__(self, directory: Path) -> None:
        self.telegram_writer = JsonlAuditWriter(directory / "telegram.jsonl")
        self.error_writer = JsonlAuditWriter(directory / "errors.jsonl")

    def prepare(self) -> None:
        self.telegram_writer.prepare()
        self.error_writer.prepare()


class TextGenerationModel(Protocol):
    def generate(self, *, prompt: str, model: str) -> str: ...


class PromptTraceContext(ContractModel):
    request_id: str
    conversation_id: str
    profile_id: str
    prompt_version: str
    database_name: str
    database_build_id: str
    graph_schema_version: str


_TRACE_CONTEXT: contextvars.ContextVar[PromptTraceContext | None] = (
    contextvars.ContextVar("gateway_prompt_trace", default=None)
)
_TRACE_ATTEMPT: contextvars.ContextVar[int] = contextvars.ContextVar(
    "gateway_prompt_attempt", default=0
)


class PromptTraceBinder:
    @contextmanager
    def bind(self, context: PromptTraceContext) -> Iterator[None]:
        context_token = _TRACE_CONTEXT.set(context)
        attempt_token = _TRACE_ATTEMPT.set(0)
        try:
            yield
        finally:
            _TRACE_ATTEMPT.reset(attempt_token)
            _TRACE_CONTEXT.reset(context_token)


class TracingGenerationModel:
    def __init__(self, model: TextGenerationModel, writer: JsonlAuditWriter) -> None:
        self._model = model
        self._writer = writer

    def generate(self, *, prompt: str, model: str) -> str:
        context = _TRACE_CONTEXT.get()
        if context is None:
            raise RuntimeError("prompt trace context is missing")
        attempt = _TRACE_ATTEMPT.get() + 1
        _TRACE_ATTEMPT.set(attempt)
        try:
            response = self._model.generate(prompt=prompt, model=model)
        except Exception:
            self._capture(context, prompt, attempt, "failed")
            raise
        self._capture(context, prompt, attempt, "captured")
        return response

    def _capture(
        self,
        context: PromptTraceContext,
        prompt: str,
        attempt: int,
        status: Literal["captured", "failed"],
    ) -> None:
        self._writer.append(
            PromptTraceEvent.model_validate(
                {
                    **event_envelope(
                        component="backend",
                        event_type="generation_prompt",
                        request_id=context.request_id,
                        conversation_id=context.conversation_id,
                        status=status,
                    ),
                    "attempt": attempt,
                    "profile_id": context.profile_id,
                    "prompt_version": context.prompt_version,
                    "database_name": context.database_name,
                    "database_build_id": context.database_build_id,
                    "graph_schema_version": context.graph_schema_version,
                    "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "rendered_prompt": prompt,
                }
            )
        )
