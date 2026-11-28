from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.backend.helpers import manifest
from vue_rag.backend.audit import (
    BackendEvent,
    ErrorEvent,
    FeedbackEvent,
    GatewayAudit,
    JsonlAuditWriter,
    PromptTraceBinder,
    PromptTraceContext,
    PromptTraceEvent,
    TracingGenerationModel,
    event_envelope,
)
from vue_rag.ports.models import GenerationResult


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, prompt: str, model: str) -> GenerationResult:
        self.calls += 1
        return GenerationResult(text="answer", provider="test")


def _error(index: int) -> ErrorEvent:
    return ErrorEvent.model_validate(
        {
            **event_envelope(
                component="backend",
                event_type="error",
                request_id=f"request-{index}",
                conversation_id=str(index),
                status="error",
            ),
            "operation": "test",
            "error_code": "failure",
            "message": "safe",
        }
    )


def _backend(request_id: str = "request-1") -> BackendEvent:
    database = manifest()
    return BackendEvent.model_validate(
        {
            **event_envelope(
                component="backend",
                event_type="message",
                request_id=request_id,
                conversation_id="-100",
                status="success",
            ),
            "telegram_user_id": 42,
            "question": "question",
            "answer": "answer",
            "profile_id": "support-v1",
            "prompt_version": "support-v1",
            "database_name": database.database_name,
            "database_build_id": database.build_id,
            "graph_schema_version": database.graph_schema_version,
            "model_id": "chat:v1",
            "latency_ms": 10,
            "retrieval_count": 1,
            "evidence_ids": ("embedding",),
        }
    )


def test_parallel_jsonl_appends_preserve_one_valid_object_per_line(
    tmp_path: Path,
) -> None:
    writer = JsonlAuditWriter(tmp_path / "errors.jsonl")
    writer.prepare()

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(writer.append, (_error(index) for index in range(100))))

    records = [
        ErrorEvent.model_validate_json(line)
        for line in writer.path.read_text().splitlines()
    ]
    assert len(records) == 100
    assert len({record.event_id for record in records}) == 100
    assert writer.path.stat().st_mode & 0o077 == 0


def test_tracing_model_writes_full_prompt_only_to_prompt_trace(tmp_path: Path) -> None:
    audit = GatewayAudit(tmp_path)
    audit.prepare()
    model = _Model()
    traced = TracingGenerationModel(model, audit.prompt_writer)
    context = PromptTraceContext(
        request_id="request-1",
        conversation_id="-100",
        profile_id="support-v1",
        prompt_version="support-v1",
        database_name="gateway-test",
        database_build_id="a" * 64,
        graph_schema_version="mvp-v2-graph-v2",
    )

    with PromptTraceBinder().bind(context):
        result = traced.generate(prompt="full secret prompt", model="chat")

    assert result.text == "answer"
    assert result.request_id == "request-1"

    trace = PromptTraceEvent.model_validate_json(
        audit.prompt_writer.path.read_text().splitlines()[0]
    )
    assert trace.rendered_prompt == "full secret prompt"
    assert trace.attempt == 1
    assert "full secret prompt" not in audit.backend_writer.path.read_text()
    assert model.calls == 1


def test_completed_request_index_rebuilds_with_owner_and_feedback_state(
    tmp_path: Path,
) -> None:
    first = GatewayAudit(tmp_path)
    first.prepare()
    first.backend_writer.append(_backend("pending-request"))
    first.backend_writer.append(_backend("rated-request"))
    first.feedback_writer.append(
        FeedbackEvent.model_validate(
            {
                **event_envelope(
                    component="backend",
                    event_type="feedback",
                    request_id="rated-request",
                    conversation_id="-100",
                    status="accepted",
                ),
                "telegram_user_id": 42,
                "rating": 4,
                "comment": None,
            }
        )
    )

    restarted = GatewayAudit(tmp_path)
    restarted.prepare()

    assert restarted.completed.claim("pending-request", 42, "wrong") is False
    assert restarted.completed.claim("pending-request", 42, "-100") is True
    assert restarted.completed.claim("rated-request", 42, "-100") is False


def test_jsonl_writer_retries_short_os_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = JsonlAuditWriter(tmp_path / "errors.jsonl")
    writer.prepare()
    real_write = os.write

    def short_write(descriptor: int, payload: bytes | memoryview) -> int:
        return real_write(descriptor, payload[:3])

    monkeypatch.setattr("vue_rag.backend.audit.os.write", short_write)
    writer.append(_error(1))

    record = ErrorEvent.model_validate_json(writer.path.read_text())
    assert record.request_id == "request-1"
