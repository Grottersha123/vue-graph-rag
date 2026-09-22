from __future__ import annotations

import re

from mvp_v2.application.conversation.errors import ChatError
from mvp_v2.application.dto.chat import Citation
from mvp_v2.application.dto.retrieval import Evidence

_CITATION_TOKEN = re.compile(r"\[([^\[\]]*)\]")
_CITATION_CONTENT = re.compile(r"[1-9]\d*(?:\s*,\s*[1-9]\d*)*", re.ASCII)


def referenced_numbers(answer: str, evidence_count: int) -> tuple[int, ...]:
    ordered: list[int] = []
    for match in _CITATION_TOKEN.finditer(answer):
        numbers = _numbers_in_token(match.group(1))
        for number in numbers:
            if number > evidence_count:
                raise ChatError("generated answer uses an invalid evidence number")
            if number not in ordered:
                ordered.append(number)
    if not ordered:
        raise ChatError("generated answer cites no valid evidence number")
    return tuple(ordered)


def render_answer(answer: str, evidence: tuple[Evidence, ...]) -> str:
    referenced_numbers(answer, len(evidence))

    def replace_token(match: re.Match[str]) -> str:
        numbers = _numbers_in_token(match.group(1))
        if not numbers:
            return match.group(0)
        references = tuple(
            _render_evidence_reference(evidence[number - 1]) for number in numbers
        )
        if len(references) == 1:
            return f"(эту информацию можно найти в {references[0]})"
        return (
            "(эту информацию можно найти в источниках: " + "; ".join(references) + ")"
        )

    return _CITATION_TOKEN.sub(replace_token, answer)


def render_citation_details(citation: Citation) -> str:
    source_ref = citation.source_url.strip() or citation.local_path
    reference = _render_reference(
        document_title=citation.document_title,
        document_id=citation.document_id,
        version=citation.version,
        position=citation.matched_chunk_position,
        chunk_count=citation.document_chunk_count,
        source_ref=source_ref,
    )
    return (
        f"Документ {reference}; score={citation.score:.6f}; "
        f"context={_one_line(citation.context_scope)}; "
        f"evidence ID={_one_line(citation.evidence_id)}"
    )


def _numbers_in_token(token: str) -> tuple[int, ...]:
    if _CITATION_CONTENT.fullmatch(token) is None:
        return ()
    ordered: list[int] = []
    for part in token.split(","):
        number = int(part.strip())
        if number not in ordered:
            ordered.append(number)
    return tuple(ordered)


def _render_evidence_reference(evidence: Evidence) -> str:
    source_ref = evidence.source_url.strip() or evidence.local_path
    return "документе " + _render_reference(
        document_title=evidence.document_title,
        document_id=evidence.document_id,
        version=evidence.version,
        position=evidence.matched_chunk_position,
        chunk_count=evidence.document_chunk_count,
        source_ref=source_ref,
    )


def _render_reference(
    *,
    document_title: str,
    document_id: str,
    version: int,
    position: int | None,
    chunk_count: int,
    source_ref: str,
) -> str:
    ordinal = "не указан" if position is None else str(position + 1)
    stored_position = "не указана" if position is None else str(position)
    return (
        f"«{_one_line(document_title)}» (ID {_one_line(document_id)}), "
        f"версия {version}, чанк {ordinal} из {chunk_count}, "
        f"фактическая позиция {stored_position}, источник {_one_line(source_ref)}"
    )


def _one_line(value: str) -> str:
    return " ".join(value.split())
