from __future__ import annotations

import hashlib
from dataclasses import dataclass

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from vue_rag.domain.base import stable_id
from vue_rag.domain.graph import Chunk


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkPolicy:
    max_characters: int = 1200
    overlap_characters: int = 120
    minimum_characters: int = 40

    def __post_init__(self) -> None:
        if (
            self.max_characters < 1
            or not 0 <= self.overlap_characters < self.max_characters
        ):
            raise ValueError("overlap must be non-negative and smaller than max")
        if not 1 <= self.minimum_characters <= self.max_characters:
            raise ValueError("minimum must be between one and max")


DEFAULT_CHUNK_POLICY = ChunkPolicy()


def split_text(
    text: str, policy: ChunkPolicy = DEFAULT_CHUNK_POLICY
) -> tuple[str, ...]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ()
    splitter = RecursiveCharacterTextSplitter.from_language(
        Language.MARKDOWN,
        chunk_size=policy.max_characters,
        chunk_overlap=policy.overlap_characters,
    )
    chunks = tuple(chunk for chunk in splitter.split_text(normalized) if chunk.strip())
    if any(len(chunk) > policy.max_characters for chunk in chunks):
        raise RuntimeError("Markdown splitter produced an oversized chunk")
    return chunks


def make_chunks(version_id: str, texts: tuple[str, ...]) -> tuple[Chunk, ...]:
    return tuple(
        Chunk(
            id=stable_id(
                "chunk",
                version_id,
                str(position),
                hashlib.sha256(text.encode()).hexdigest(),
            ),
            position=position,
            content=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
        for position, text in enumerate(texts)
    )
