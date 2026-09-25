from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from mvp_v2.analytics.chat_logs import ChatLogAnalytics, standard_reports
from mvp_v2.backend.audit import (
    BackendEvent,
    ErrorEvent,
    FeedbackEvent,
    GatewayAudit,
    JsonlAuditWriter,
    PromptTraceBinder,
    PromptTraceContext,
    PromptTraceEvent,
    TelegramEvent,
    TracingGenerationModel,
    event_envelope,
)
from tests.backend.helpers import manifest


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, prompt: str, model: str) -> str:
        self.calls += 1
        return "answer"


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
        assert traced.generate(prompt="full secret prompt", model="chat") == "answer"

    trace = PromptTraceEvent.model_validate_json(
        audit.prompt_writer.path.read_text().splitlines()[0]
    )
    assert trace.rendered_prompt == "full secret prompt"
    assert trace.attempt == 1
    assert "full secret prompt" not in audit.backend_writer.path.read_text()
    assert model.calls == 1


def test_duckdb_reports_and_debug_tolerate_missing_or_corrupt_trace(
    tmp_path: Path,
) -> None:
    audit = GatewayAudit(tmp_path)
    audit.prepare()
    for request_id, rating in (
        ("request-1", 5),
        ("request-2", 1),
        ("request-3", 1),
    ):
        audit.backend_writer.append(_backend(request_id))
        audit.feedback_writer.append(
            FeedbackEvent.model_validate(
                {
                    **event_envelope(
                        component="backend",
                        event_type="feedback",
                        request_id=request_id,
                        conversation_id="-100",
                        status="accepted",
                    ),
                    "telegram_user_id": 42,
                    "rating": rating,
                    "comment": None,
                }
            )
        )
    audit.error_writer.append(_error(1))
    telegram = JsonlAuditWriter(tmp_path / "telegram.jsonl")
    telegram.prepare()
    telegram.append(
        TelegramEvent.model_validate(
            {
                **event_envelope(
                    component="telegram",
                    event_type="transport",
                    request_id="request-1",
                    conversation_id="-100",
                    status="received",
                ),
                "user_id": 42,
                "chat_id": -100,
                "source_message_id": 7,
                "action": "message_received",
                "delivery_status": "received",
            }
        )
    )
    telegram.append(
        TelegramEvent.model_validate(
            {
                **event_envelope(
                    component="telegram",
                    event_type="transport",
                    request_id="callback-1",
                    conversation_id="-100",
                    status="sent",
                ),
                "user_id": 42,
                "chat_id": -100,
                "source_message_id": 7,
                "action": "feedback_sent",
                "delivery_status": "sent",
                "related_request_id": "request-1",
            }
        )
    )
    audit.prompt_writer.path.write_text("{corrupt json\n", encoding="utf-8")
    analytics = ChatLogAnalytics(tmp_path)

    missing = analytics.debug_request("request-1")
    reports = standard_reports(tmp_path)

    assert missing is not None
    assert missing["prompt_available"] is False
    telegram_events = cast(tuple[dict[str, object], ...], missing["telegram"])
    assert len(telegram_events) == 2
    assert reports["daily_status"][0]["status"] == "success"
    assert reports["daily_status"][0]["requests"] == 3
    assert reports["latency"] == (
        {"status": "success", "average_ms": 10.0, "p95_ms": 10.0},
    )
    assert reports["errors"][0]["error_code"] == "failure"
    assert reports["profiles"] == ({"profile_id": "support-v1", "requests": 3},)
    assert reports["telegram_users"] == ({"telegram_user_id": 42, "requests": 3},)
    assert reports["feedback"] == (
        {
            "profile_id": "support-v1",
            "rating": 1,
            "ratings": 2,
            "average_rating": 7 / 3,
        },
        {
            "profile_id": "support-v1",
            "rating": 5,
            "ratings": 1,
            "average_rating": 7 / 3,
        },
    )
    assert reports["feedback_periods"][0]["average_rating"] == 7 / 3
    debug_errors = cast(tuple[dict[str, object], ...], missing["errors"])
    assert debug_errors[0]["error_code"] == "failure"

    first_trace = PromptTraceEvent.model_validate(
        {
            **event_envelope(
                component="backend",
                event_type="generation_prompt",
                request_id="request-1",
                conversation_id="-100",
                status="captured",
            ),
            "attempt": 1,
            "profile_id": "support-v1",
            "prompt_version": "support-v1",
            "database_name": "gateway-test",
            "database_build_id": "a" * 64,
            "graph_schema_version": "mvp-v2-graph-v2",
            "prompt_hash": "b" * 64,
            "rendered_prompt": "prompt",
        }
    )
    final_trace = first_trace.model_copy(
        update={
            "event_ts": first_trace.event_ts + timedelta(microseconds=1),
            "attempt": 2,
            "prompt_hash": "c" * 64,
            "rendered_prompt": "retry prompt",
        }
    )
    audit.prompt_writer.path.write_text(
        first_trace.model_dump_json() + "\n" + final_trace.model_dump_json() + "\n",
        encoding="utf-8",
    )
    complete = analytics.debug_request("request-1")
    assert complete is not None
    assert complete["prompt_available"] is True
    prompt_trace = cast(dict[str, object], complete["prompt_trace"])
    assert prompt_trace["attempt"] == 2
    assert prompt_trace["rendered_prompt"] == "retry prompt"


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

    monkeypatch.setattr("mvp_v2.backend.audit.os.write", short_write)
    writer.append(_error(1))

    record = ErrorEvent.model_validate_json(writer.path.read_text())
    assert record.request_id == "request-1"
