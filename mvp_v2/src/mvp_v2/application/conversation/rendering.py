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
    return _render_reference(
        document_title=citation.document_title,
        source_url=citation.source_url,
        position=citation.matched_chunk_position,
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
    return _render_reference(
        document_title=evidence.document_title,
        source_url=evidence.source_url,
        position=evidence.matched_chunk_position,
    )


def _render_reference(
    *,
    document_title: str,
    source_url: str,
    position: int | None,
) -> str:
    source = _one_line(source_url) or f"«{_one_line(document_title)}»"
    ordinal = "не указан" if position is None else str(position + 1)
    return f"{source}, чанк {ordinal}"


def _one_line(value: str) -> str:
    return " ".join(value.split())
