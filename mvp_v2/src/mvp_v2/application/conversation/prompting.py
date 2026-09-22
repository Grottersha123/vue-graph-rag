from __future__ import annotations

from mvp_v2.application.conversation.conf import (
    DEFAULT_PROMPT_TEMPLATE,
    PromptTemplate,
    allowed_tokens,
    evidence_block,
    render_prompt,
    render_retry,
)
from mvp_v2.application.conversation.errors import ChatError
from mvp_v2.application.dto.retrieval import Evidence


def build_chat_prompt(
    question: str,
    evidence: tuple[Evidence, ...],
    *,
    max_evidence_chars: int,
    max_prompt_chars: int,
    template: PromptTemplate = DEFAULT_PROMPT_TEMPLATE,
) -> str:
    blocks: list[str] = []
    total = 0
    for number, item in enumerate(evidence, 1):
        total += len(item.text)
        if total > max_evidence_chars:
            raise ChatError("evidence exceeds configured character limit")
        blocks.append(evidence_block(number, item.text))
    prompt = render_prompt(
        template,
        question=question,
        blocks=blocks,
        allowed=allowed_tokens(len(evidence)),
    )
    if len(prompt) > max_prompt_chars:
        raise ChatError("prompt exceeds configured character limit")
    return prompt


def build_citation_retry_prompt(
    prompt: str,
    evidence_count: int,
    *,
    max_prompt_chars: int,
    template: PromptTemplate = DEFAULT_PROMPT_TEMPLATE,
) -> str:
    retry_prompt = render_retry(
        template,
        prompt=prompt,
        allowed=allowed_tokens(evidence_count),
    )
    if len(retry_prompt) > max_prompt_chars:
        raise ChatError("citation retry prompt exceeds configured character limit")
    return retry_prompt
