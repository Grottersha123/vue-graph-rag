from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from vue_rag.application.dto.chat import ChatAnswer
from vue_rag.application.dump_lifecycle.manifests import DatabaseManifest
from vue_rag.application.rag.agent.answer_rendering import render_citation_details


class ChatTurnProvenance(BaseModel):
    """Immutable model/database identity captured when an answer is created."""

    model_config = ConfigDict(frozen=True)

    chat_model: str = ""
    chat_model_version: str = ""
    database_name: str = ""
    source_dump_number: int | None = None
    database_built_at: str = ""
    embedding_space: str = ""
    vision_models: str = ""

    @classmethod
    def capture(
        cls,
        *,
        manifest: DatabaseManifest | None,
        chat_model: str,
        chat_model_version: str,
    ) -> ChatTurnProvenance:
        if manifest is None:
            return cls(chat_model=chat_model, chat_model_version=chat_model_version)
        space = manifest.embedding_space
        return cls(
            chat_model=chat_model,
            chat_model_version=chat_model_version,
            database_name=manifest.database_name,
            source_dump_number=manifest.source_dump_number,
            database_built_at=str(manifest.built_at),
            embedding_space=(
                f"{space.provider} / {space.model} / {space.model_version} / "
                f"dim {space.dimension}"
            ),
            vision_models=(
                f"{manifest.vision_provider} / {manifest.vision_model} / "
                f"{manifest.vision_model_version}"
            ),
        )

    def lines(self) -> list[str]:
        dump = (
            f"dump-{self.source_dump_number:06d}"
            if self.source_dump_number is not None
            else "not recorded"
        )
        return [
            f"- Chat model: {self.chat_model or 'not recorded'} "
            f"(version {self.chat_model_version or 'not recorded'})",
            f"- Database: {self.database_name or 'not recorded'}",
            f"- Source dump: {dump}",
        ] + (
            [
                f"- Database built at: {self.database_built_at}",
                f"- Embeddings: {self.embedding_space}",
                f"- Vision: {self.vision_models}",
            ]
            if self.database_name
            else []
        )


def render_chat_markdown(
    history: Sequence[tuple[str, ChatAnswer, ChatTurnProvenance]],
    *,
    generated_at: datetime | None = None,
) -> str:
    moment = (generated_at or datetime.now(UTC)).astimezone(UTC)
    lines = [
        "# Graph RAG chat export",
        "",
        f"- Exported: {moment.isoformat(timespec='seconds').replace('+00:00', 'Z')}",
        f"- Answers: {len(history)}",
        "",
    ]
    for index, (question, answer, provenance) in enumerate(history, start=1):
        lines += [
            f"## {index}. {question}",
            "",
            f"Generated: {'yes' if answer.generated else 'no'}",
            *provenance.lines(),
            "",
            answer.answer,
            "",
        ]
        if answer.citations:
            lines.append("Citations:")
            for citation in answer.citations:
                lines.append(f"- {render_citation_details(citation)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
