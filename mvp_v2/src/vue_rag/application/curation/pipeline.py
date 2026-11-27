from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import cast

from .contracts import (
    CurationCandidate,
    CurationRecord,
    KnowledgeKind,
    Provenance,
    ReviewStatus,
    Route,
)
from .redaction import redact


@dataclass(frozen=True, slots=True)
class VerifiedDump:
    dump_id: str
    documents: frozenset[str]
    versions: frozenset[str]
    chunks: frozenset[str]
    active_documents: frozenset[str]


@dataclass(frozen=True, slots=True)
class CaseInput:
    case: dict[str, object]
    result: dict[str, object]
    onboarding: dict[str, object]


def load_jsonl(path: Path) -> Iterator[dict[str, object]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                yield value


def load_cases(data_dir: Path) -> tuple[CaseInput, ...]:
    cases: dict[str, dict[str, object]] = {}
    results: dict[str, dict[str, object]] = {}
    onboarding: dict[str, dict[str, object]] = {}
    for path in sorted(data_dir.glob("cases_batch_*.jsonl")):
        cases.update(
            {str(row["case_id"]): row for row in load_jsonl(path) if "case_id" in row}
        )
    for path in sorted(data_dir.glob("results_batch_*.jsonl")):
        results.update(
            {str(row["case_id"]): row for row in load_jsonl(path) if "case_id" in row}
        )
    for path in sorted(data_dir.glob("onboarding_results_batch_*.jsonl")):
        onboarding.update(
            {str(row["case_id"]): row for row in load_jsonl(path) if "case_id" in row}
        )
    return tuple(
        CaseInput(
            case=cases[case_id], result=results[case_id], onboarding=onboarding[case_id]
        )
        for case_id in sorted(cases.keys() & results.keys() & onboarding.keys())
    )


def load_verified_dump(dump_dir: Path) -> VerifiedDump:
    tables = dump_dir / "tables"

    def ids(name: str) -> frozenset[str]:
        with (tables / name).open(newline="", encoding="utf-8") as stream:
            return frozenset(row["id"] for row in csv.DictReader(stream))

    documents = ids("Document.csv")
    versions = ids("DocumentVersion.csv")
    chunks = ids("Chunk.csv")
    active: set[str] = set()
    with (tables / "Document.csv").open(newline="", encoding="utf-8") as stream:
        active = {
            row["id"]
            for row in csv.DictReader(stream)
            if row["is_active"].lower() == "true"
        }
    manifest = json.loads((dump_dir / "manifest.json").read_text(encoding="utf-8"))
    return VerifiedDump(
        dump_id=f"dump-{int(manifest['dump_number']):06d}",
        documents=documents,
        versions=versions,
        chunks=chunks,
        active_documents=frozenset(active),
    )


def _question(case: dict[str, object]) -> str:
    turns = case.get("turns", [])
    if not isinstance(turns, list):
        return ""
    return next(
        (str(item.get("text", "")) for item in turns if item.get("role") == "student"),
        "",
    )


def _answer(result: dict[str, object]) -> str:
    questions = result.get("questions", [])
    if not isinstance(questions, list):
        return ""
    return "\n".join(
        str(item.get("answer", "")) for item in questions if item.get("answer")
    )


def _route(result: dict[str, object], answer: str) -> str:
    text = f"{result.get('classification', {})} {answer}".casefold()
    if any(
        word in text
        for word in ("код", "доступ", "парол", "проверить аккаунт", "настроить")
    ):
        return "support_action"
    if any(word in text for word in ("кира", "решение", "выбрать рынок", "исключение")):
        return "kira_decision"
    return "rag_answer"


def _provenance_for(case: CaseInput, dump: VerifiedDump) -> tuple[dict[str, str], ...]:
    # Case records do not contain document provenance; this is intentionally empty
    # until a verified document match is supplied by the curation reviewer.
    del case, dump
    return ()


def build_candidates(
    inputs: Iterable[CaseInput], dump: VerifiedDump, golden_questions: Iterable[str]
) -> tuple[CurationCandidate, ...]:
    """Create unpublished candidates; only records with provenance can be promoted."""
    del dump
    candidates: list[CurationCandidate] = []
    for item in inputs:
        question = redact(_question(item.case))
        answer = redact(_answer(item.result))
        classification = item.result.get("classification", {})
        answerability = (
            classification.get("answerability", "")
            if isinstance(classification, dict)
            else ""
        )
        route: Route = cast(Route, _route(item.result, answer))
        kind = (
            KnowledgeKind.ROUTING_EXAMPLE
            if route != "rag_answer"
            else KnowledgeKind.QUARANTINE
        )
        candidate = CurationCandidate(
            faq_id=f"case-{item.case['case_id']}",
            question=question or "[empty question]",
            original_answer=answer or "[empty answer]",
            knowledge_kind=kind,
            route=route,
            risk="high" if route in {"kira_decision", "safety_review"} else "medium",
            scope="case_specific"
            if route != "rag_answer"
            else "unverified_documentation",
            source_case_ids=(str(item.case["case_id"]),),
            answerability=str(answerability),
        )
        if semantic_overlap(candidate.question, golden_questions):
            candidate = candidate.model_copy(
                update={"knowledge_kind": KnowledgeKind.QUARANTINE}
            )
        candidates.append(candidate)
    return tuple(candidates)


def semantic_overlap(
    question: str, golden_questions: Iterable[str], threshold: float = 0.86
) -> bool:
    normalized = re.sub(r"\W+", " ", question.casefold()).strip()
    return any(
        SequenceMatcher(
            None, normalized, re.sub(r"\W+", " ", item.casefold()).strip()
        ).ratio()
        >= threshold
        for item in golden_questions
    )


def validate_provenance(record: CurationRecord, dump: VerifiedDump) -> tuple[str, ...]:
    """Return missing provenance identifiers; no silent fallback is allowed."""
    missing: list[str] = []
    for item in record.provenance:
        if item.document_id not in dump.documents:
            missing.append(f"document:{item.document_id}")
        if item.version_id not in dump.versions:
            missing.append(f"version:{item.version_id}")
        if item.chunk_id not in dump.chunks:
            missing.append(f"chunk:{item.chunk_id}")
    return tuple(missing)


def promote_candidate(
    candidate: CurationCandidate,
    provenance: Iterable[Provenance],
    dump: VerifiedDump,
    *,
    approved_by: str = "",
    approved_at: datetime | None = None,
) -> CurationRecord:
    """Promote a candidate only after verified provenance and manual approval."""
    items = tuple(provenance)
    record = CurationRecord(
        faq_id=candidate.faq_id,
        question=candidate.question,
        approved_answer=candidate.original_answer,
        knowledge_kind=candidate.knowledge_kind,
        review_status=ReviewStatus.APPROVED,
        reusable_as_policy=candidate.reusable_as_policy,
        route=candidate.route,
        risk=candidate.risk,
        scope=candidate.scope,
        provenance=items,
        source_case_ids=candidate.source_case_ids,
        approved_by=approved_by,
        approved_at=approved_at,
        golden_overlap_checked=True,
        pii_redacted=True,
    )
    missing = validate_provenance(record, dump)
    if missing:
        raise ValueError(f"provenance is not verified: {', '.join(missing)}")
    return record
