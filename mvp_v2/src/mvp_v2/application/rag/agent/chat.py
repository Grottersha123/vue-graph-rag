from __future__ import annotations

from typing import Final, Protocol

from mvp_v2.application.dto.chat import ChatAnswer, ChatRequest, Citation
from mvp_v2.application.dto.retrieval import Evidence, SearchRequest
from mvp_v2.application.rag.agent.answer_rendering import (
    referenced_numbers,
    render_answer,
)
from mvp_v2.application.rag.agent.errors import ChatError
from mvp_v2.application.rag.agent.prompts import (
    DEFAULT_PROMPT_TEMPLATE,
    INSUFFICIENT_EVIDENCE_TOKEN,
    PromptTemplate,
    build_chat_prompt,
    build_citation_retry_prompt,
)
from mvp_v2.ports.models import TextGenerationModel

NOT_ENOUGH_EVIDENCE: Final = (
    "Мне пока не хватает подтверждённой информации, чтобы надёжно ответить. "
    "Попробуйте уточнить вопрос или добавить подходящие материалы."
)
MAX_EVIDENCE_ITEMS: Final = 8


class EvidenceSearch(Protocol):
    def search(self, request: SearchRequest) -> tuple[Evidence, ...]: ...


class ChatService:
    def __init__(
        self,
        search: EvidenceSearch,
        model: TextGenerationModel,
        *,
        max_question_chars: int = 2_000,
        max_evidence_items: int = MAX_EVIDENCE_ITEMS,
        max_evidence_chars: int = 12_000,
        max_prompt_chars: int = 16_000,
        prompt_template: PromptTemplate = DEFAULT_PROMPT_TEMPLATE,
    ) -> None:
        if (
            min(
                max_question_chars,
                max_evidence_items,
                max_evidence_chars,
                max_prompt_chars,
            )
            < 1
        ):
            raise ValueError("chat limits must be positive")
        self._search = search
        self._model = model
        self._max_question_chars = max_question_chars
        self._max_evidence_items = max_evidence_items
        self._max_evidence_chars = max_evidence_chars
        self._max_prompt_chars = max_prompt_chars
        self._prompt_template = prompt_template

    def answer(self, request: ChatRequest) -> ChatAnswer:
        if len(request.question) > self._max_question_chars:
            raise ChatError("question exceeds configured character limit")
        evidence = self._search.search(request.search)
        if not evidence:
            return _insufficient_evidence_answer()
        evidence = evidence[: self._max_evidence_items]
        if (
            not request.authorization.allow
            or request.authorization.operation != "generation"
        ):
            raise ChatError("explicit generation authorization is required")
        prompt = self._prompt(request.question, evidence)
        response = self._model.generate(prompt=prompt, model=request.model)
        if not response.strip():
            raise ChatError("local generation returned an empty response")
        raw_answer = response.strip()
        if raw_answer == INSUFFICIENT_EVIDENCE_TOKEN:
            return _insufficient_evidence_answer()
        try:
            referenced = referenced_numbers(raw_answer, len(evidence))
        except ChatError:
            retry_prompt = self._citation_retry_prompt(prompt, evidence)
            response = self._model.generate(prompt=retry_prompt, model=request.model)
            if not response.strip():
                raise ChatError("local generation returned an empty response") from None
            raw_answer = response.strip()
            if raw_answer == INSUFFICIENT_EVIDENCE_TOKEN:
                return _insufficient_evidence_answer()
            referenced = referenced_numbers(raw_answer, len(evidence))
        answer = render_answer(raw_answer, evidence)
        citations = tuple(
            _citation(number, evidence[number - 1]) for number in referenced
        )
        selected_evidence = tuple(evidence[number - 1] for number in referenced)
        return ChatAnswer(
            answer=answer,
            citations=citations,
            evidence=selected_evidence,
            generated=True,
        )

    def _prompt(self, question: str, evidence: tuple[Evidence, ...]) -> str:
        return build_chat_prompt(
            question,
            evidence,
            max_evidence_chars=self._max_evidence_chars,
            max_prompt_chars=self._max_prompt_chars,
            template=self._prompt_template,
        )

    def _citation_retry_prompt(
        self, prompt: str, evidence: tuple[Evidence, ...]
    ) -> str:
        return build_citation_retry_prompt(
            prompt,
            len(evidence),
            max_prompt_chars=self._max_prompt_chars,
            template=self._prompt_template,
        )


def _insufficient_evidence_answer() -> ChatAnswer:
    return ChatAnswer(
        answer=NOT_ENOUGH_EVIDENCE,
        citations=(),
        evidence=(),
        generated=False,
    )


def _citation(number: int, evidence: Evidence) -> Citation:
    return Citation(
        number=number,
        evidence_id=evidence.embedding_id,
        score=evidence.score,
        matched_chunk_position=evidence.matched_chunk_position,
        document_chunk_count=evidence.document_chunk_count,
        context_scope=evidence.context_scope,
        document_id=evidence.document_id,
        document_title=evidence.document_title,
        version_id=evidence.version_id,
        version=evidence.version,
        source_url=evidence.source_url,
        local_path=evidence.local_path,
    )
