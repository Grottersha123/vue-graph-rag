from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vue_rag.application.dto.chat import ChatAnswer, ChatRequest
from vue_rag.application.dto.retrieval import (
    EmbeddingSpaceIdentity,
    Evidence,
    SubjectKind,
    SubjectRef,
)
from vue_rag.application.dump_lifecycle.manifests import (
    DATABASE_FORMAT,
    DatabaseManifest,
    TableCount,
    compute_database_build_id,
)
from vue_rag.backend.audit import GatewayAudit
from vue_rag.backend.profiles import ProfileRegistry
from vue_rag.backend.service import CapacityLimiter, GatewayChatService, NullTraceBinder
from vue_rag.domain.schema_registry import GRAPH_SCHEMA_VERSION, TABLE_NAMES

NOW = datetime(2026, 9, 24, tzinfo=UTC)
SPACE = EmbeddingSpaceIdentity(
    provider="ollama", model="embed", model_version="v1", dimension=2
)


def manifest() -> DatabaseManifest:
    counts = tuple(TableCount(table=table, count=0) for table in TABLE_NAMES)
    values: dict[str, Any] = {
        "database_name": "gateway-test",
        "built_at": NOW,
        "source_dump_number": 1,
        "source_dump_published_at": NOW,
        "source_dump_run_id": "a" * 64,
        "embedding_space": SPACE,
        "vision_provider": "ollama",
        "vision_model": "vision",
        "vision_model_version": "v1",
        "row_counts": counts,
    }
    return DatabaseManifest(
        format=DATABASE_FORMAT,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        build_id=compute_database_build_id(**values),
        **values,
    )


def evidence() -> Evidence:
    return Evidence(
        subject=SubjectRef(kind=SubjectKind.CHUNK, id="chunk"),
        embedding_id="embedding",
        score=0.9,
        text="Evidence text",
        matched_chunk_position=0,
        document_chunk_count=1,
        context_scope="full_document",
        document_id="document",
        document_title="Document",
        external_id="doc:document",
        knowledge_kind="official_documentation",
        source_kind="documentation",
        source_url="https://example.test/document",
        version_id="version",
        version=1,
        local_path="documents/document.md",
        source_type="local_file",
        statuses=("ready",),
        roles=("student",),
        processes=("onboarding",),
        linked_document_ids=(),
    )


class FakeChat:
    def __init__(self, error: Exception | None = None) -> None:
        self.requests: list[ChatRequest] = []
        self._error = error

    def answer(self, request: ChatRequest) -> ChatAnswer:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return ChatAnswer(
            answer="Verified answer.",
            citations=(),
            evidence=(evidence(),),
            generated=True,
            provider="test",
        )


class MemoryProfileStore:
    def __init__(self) -> None:
        self.values: dict[int, str] = {}
        self.ready_value = True

    def read(self, telegram_user_id: int) -> str | None:
        return self.values.get(telegram_user_id)

    def write(self, telegram_user_id: int, profile_id: str) -> None:
        self.values[telegram_user_id] = profile_id

    def ready(self) -> bool:
        return self.ready_value


class ReadyProbe:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def check(self):  # type: ignore[no-untyped-def]
        from vue_rag.backend.contracts import HealthHttpResponse

        return HealthHttpResponse(
            status="ready" if self.ready else "not_ready",
            database=True,
            ollama=self.ready,
            redis=self.ready,
            audit=True,
        )


def gateway(
    tmp_path: Path,
    *,
    chat: FakeChat | None = None,
    store: MemoryProfileStore | None = None,
    capacity: CapacityLimiter | None = None,
) -> tuple[GatewayChatService, FakeChat, MemoryProfileStore, GatewayAudit]:
    selected_chat = chat or FakeChat()
    selected_store = store or MemoryProfileStore()
    audit = GatewayAudit(tmp_path)
    audit.prepare()
    service = GatewayChatService(
        chats={"support-v1": selected_chat},
        manifest=manifest(),
        profiles=ProfileRegistry(),
        profile_store=selected_store,
        audit=audit,
        trace_binder=NullTraceBinder(),
        capacity=capacity or CapacityLimiter(6, {"chat": 2}),
        allowed_user_ids=frozenset({42}),
        chat_model="chat",
        chat_model_version="v1",
        evidence_limit=8,
        minimum_score=0.55,
    )
    return service, selected_chat, selected_store, audit
