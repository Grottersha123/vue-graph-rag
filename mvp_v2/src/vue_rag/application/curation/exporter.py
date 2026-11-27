from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .contracts import CurationRecord, KnowledgeKind, ReviewStatus
from .redaction import redact

_FAQ_KINDS = frozenset(
    {KnowledgeKind.CURATED_FAQ, KnowledgeKind.OFFICIAL_DOCUMENTATION}
)
_SECTION_HEADING = re.compile(r"(?m)^##[ \t]+.+$")


@dataclass(frozen=True, slots=True)
class ExportPaths:
    root: Path

    @property
    def faq(self) -> Path:
        return self.root / "faq"

    @property
    def routing_examples(self) -> Path:
        return self.root / "routing_examples"

    @property
    def quarantine(self) -> Path:
        return self.root / "quarantine"

    def create(self) -> None:
        for path in (self.faq, self.routing_examples, self.quarantine):
            path.mkdir(parents=True, exist_ok=True)


def split_faq_sections(text: str) -> tuple[str, ...]:
    """Split Markdown into atomic H2 FAQ units, keeping question and answer."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    starts = [match.start() for match in _SECTION_HEADING.finditer(normalized)]
    stops = [*starts[1:], len(normalized)]
    return tuple(
        section
        for start, stop in zip(starts, stops, strict=True)
        if (section := normalized[start:stop].strip())
    )


def _safe_record(record: CurationRecord) -> CurationRecord:
    return record.model_copy(
        update={
            "question": redact(record.question),
            "approved_answer": redact(record.approved_answer),
            "pii_redacted": True,
        }
    )


def validate_records(
    records: Iterable[CurationRecord],
    golden_questions: Iterable[str] = (),
) -> tuple[CurationRecord, ...]:
    """Redact PII and quarantine records that duplicate golden questions."""

    golden = {redact(item).casefold().strip() for item in golden_questions}
    result: list[CurationRecord] = []
    for raw in records:
        record = _safe_record(raw)
        if record.question.casefold().strip() in golden:
            record = record.model_copy(
                update={
                    "knowledge_kind": KnowledgeKind.QUARANTINE,
                    "review_status": ReviewStatus.NOT_VERIFIED,
                    "reusable_as_policy": False,
                }
            )
        result.append(record)
    return tuple(result)


def _target_directory(paths: ExportPaths, kind: KnowledgeKind) -> Path:
    if kind in _FAQ_KINDS:
        return paths.faq
    if kind is KnowledgeKind.ROUTING_EXAMPLE:
        return paths.routing_examples
    return paths.quarantine


def _is_publishable(record: CurationRecord) -> bool:
    return (
        record.knowledge_kind in _FAQ_KINDS
        and record.review_status is ReviewStatus.APPROVED
        and record.reusable_as_policy
    )


def export_records(
    records: Iterable[CurationRecord], paths: ExportPaths
) -> dict[str, object]:
    """Write approved FAQ Markdown and keep routing/quarantine out of the index."""

    paths.create()
    counts = {kind.value: 0 for kind in KnowledgeKind}
    published: list[str] = []
    for record in records:
        target = _target_directory(paths, record.knowledge_kind)
        (target / f"{record.faq_id}.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        counts[record.knowledge_kind.value] += 1
        if not _is_publishable(record):
            continue
        body = f"## {record.question}\n\n{record.approved_answer}\n"
        (target / f"{record.faq_id}.md").write_text(body, encoding="utf-8")
        published.append(record.faq_id)
    manifest: dict[str, object] = {
        "records": counts,
        "published_faq_ids": published,
        "record_hash": sha256("".join(sorted(published)).encode()).hexdigest(),
        "status": "VERIFIED" if published else "NOT VERIFIED",
    }
    (paths.root / "run-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
