from __future__ import annotations

from datetime import UTC, datetime

from mvp_v2.application.chat_export import (
    ChatTurnProvenance,
    render_chat_markdown,
)
from mvp_v2.application.dto.chat import ChatAnswer
from mvp_v2.application.dto.retrieval import (
    EmbeddingSpaceIdentity,
    Evidence,
    SubjectKind,
    SubjectRef,
)
from mvp_v2.application.manifests import (
    DATABASE_FORMAT,
    GRAPH_SCHEMA_VERSION,
    DatabaseManifest,
    TableCount,
    compute_database_build_id,
)
from mvp_v2.domain.schema_registry import TABLE_NAMES

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
SPACE = EmbeddingSpaceIdentity(
    provider="ollama", model="bge-m3", model_version="790764642607", dimension=1024
)


def _manifest(database_name: str, dump_number: int) -> DatabaseManifest:
    counts = tuple(TableCount(table=name, count=0) for name in TABLE_NAMES)
    values: dict[str, object] = dict(
        database_name=database_name,
        built_at=NOW,
        source_dump_number=dump_number,
        source_dump_published_at=NOW,
        source_dump_run_id="c" * 64,
        embedding_space=SPACE,
        vision_provider="ollama",
        vision_model="minicpm-v",
        vision_model_version="8b",
        row_counts=counts,
    )
    return DatabaseManifest(
        format=DATABASE_FORMAT,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        build_id=compute_database_build_id(**values),  # type: ignore[arg-type]
        **values,  # type: ignore[arg-type]
    )


def _evidence() -> Evidence:
    return Evidence(
        subject=SubjectRef(kind=SubjectKind.CHUNK, id="chunk-1"),
        embedding_id="embedding-1",
        score=0.91,
        text="факт",
        matched_chunk_position=2,
        document_chunk_count=12,
        context_scope="chunk_window",
        document_id="document-1",
        document_title="Гайд по телеграму",
        source_url="file://docs/a.md",
        version_id="version-1",
        version=2,
        local_path="documents/a.md",
        source_type="local_file",
        statuses=("ready",),
        roles=("student",),
        processes=("onboarding",),
        linked_document_ids=(),
    )


def _answer(text: str) -> ChatAnswer:
    return ChatAnswer(
        answer=text,
        citations=(),
        evidence=(_evidence(),),
        generated=True,
    )


def test_capture_reads_manifest_identity() -> None:
    provenance = ChatTurnProvenance.capture(
        manifest=_manifest("graph-rag-bge-m3-v1", 9),
        chat_model="qwen3:8b",
        chat_model_version="latest",
    )
    empty = ChatTurnProvenance.capture(
        manifest=None, chat_model="m", chat_model_version="v"
    )

    assert provenance.database_name == "graph-rag-bge-m3-v1"
    assert provenance.source_dump_number == 9
    assert provenance.embedding_space == ("ollama / bge-m3 / 790764642607 / dim 1024")
    assert empty.database_name == ""
    assert empty.source_dump_number is None


def test_export_keeps_per_answer_provenance_when_context_switches() -> None:
    first = ChatTurnProvenance.capture(
        manifest=_manifest("db-a", 1),
        chat_model="model-x",
        chat_model_version="1",
    )
    second = ChatTurnProvenance.capture(
        manifest=_manifest("db-b", 2),
        chat_model="model-y",
        chat_model_version="2",
    )

    text = render_chat_markdown(
        [
            ("Первый вопрос?", _answer("Ответ один"), first),
            ("Второй вопрос?", _answer("Ответ два"), second),
        ],
        generated_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )

    assert text.startswith("# Graph RAG chat export")
    assert "- Exported: 2026-09-15T12:00:00Z" in text
    assert "- Answers: 2" in text
    assert "## 1. Первый вопрос?" in text
    assert "- Chat model: model-x (version 1)" in text
    assert "- Database: db-a" in text
    assert "- Source dump: dump-000001" in text
    assert "- Embeddings: ollama / bge-m3 / 790764642607 / dim 1024" in text
    assert "## 2. Второй вопрос?" in text
    assert "- Chat model: model-y (version 2)" in text
    assert "- Database: db-b" in text
    assert "- Source dump: dump-000002" in text
    assert "Первая строка\nВторая строка" not in text


def test_export_renders_answer_citations_and_missing_provenance() -> None:
    from mvp_v2.application.dto.chat import Citation

    answer = _answer("Первая строка\nВторая строка")
    answer = answer.model_copy(
        update={
            "citations": (
                Citation(
                    number=1,
                    evidence_id="embedding-1",
                    score=0.91,
                    matched_chunk_position=2,
                    document_chunk_count=12,
                    context_scope="chunk_window",
                    document_id="document-1",
                    document_title="Гайд по телеграму",
                    version_id="version-1",
                    version=2,
                    source_url="file://docs/a.md",
                    local_path="documents/a.md",
                ),
            )
        }
    )

    text = render_chat_markdown(
        [("Что в гайде?", answer, ChatTurnProvenance())],
    )

    assert "## 1. Что в гайде?" in text
    assert "Первая строка\nВторая строка" in text
    assert "Generated: yes" in text
    assert "- Chat model: not recorded (version not recorded)" in text
    assert "- Database: not recorded" in text
    assert "- Source dump: not recorded" in text
    assert "- [1]" not in text
    assert "- file://docs/a.md, чанк 3" in text
    assert "Гайд по телеграму" not in text
    assert "score=" not in text
    assert "evidence ID=" not in text
