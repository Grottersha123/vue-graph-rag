from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mvp_v2.domain.base import stable_id
from mvp_v2.domain.graph import Chunk


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
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        limit = min(start + policy.max_characters, len(normalized))
        end = limit
        if limit < len(normalized):
            candidates = [
                normalized.rfind("\n\n", start, limit),
                normalized.rfind("\n", start, limit),
                normalized.rfind(" ", start, limit),
            ]
            end = max(
                (
                    item
                    for item in candidates
                    if item >= start + policy.minimum_characters
                ),
                default=limit,
            )
        value = normalized[start:end].strip()
        if value:
            chunks.append(value)
        if end == len(normalized):
            break
        start = max(end - policy.overlap_characters, start + 1)
    if len(chunks) > 1 and len(chunks[-1]) < policy.minimum_characters:
        merged = (chunks[-2] + "\n" + chunks[-1]).strip()
        if len(merged) <= policy.max_characters:
            chunks[-2:] = [merged]
    return tuple(chunks)


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
