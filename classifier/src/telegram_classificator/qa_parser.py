from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger("telegram_classificator.qa_parser")
JsonRecord = dict[str, Any]


def _flatten_source(source: Any) -> list[JsonRecord]:
    """Return source occurrences without dropping provenance fields."""
    if not isinstance(source, dict):
        return []
    occurrences = source.get("occurrences")
    if isinstance(occurrences, list):
        return [dict(item) for item in occurrences if isinstance(item, dict)]
    return [dict(source)]


def _collect_attachment_text(attachment: JsonRecord) -> list[str]:
    parts: list[str] = []
    for key in ("caption", "visible_text", "visual_summary"):
        value = (attachment.get(key) or "").strip()
        if value:
            parts.append(value)
    return parts


def collect_case_text(record: JsonRecord) -> str:
    parts: list[str] = []
    question_text = (record.get("question_text") or "").strip()
    if question_text:
        parts.append(question_text)
    for attachment in record.get("attachments") or []:
        if isinstance(attachment, dict):
            parts.extend(_collect_attachment_text(attachment))
    return "\n".join(parts).strip()


def context_block(record: JsonRecord) -> str:
    parts: list[str] = []
    for attachment in record.get("context_attachments") or []:
        if isinstance(attachment, dict):
            parts.extend(_collect_attachment_text(attachment))
    return "\n".join(parts).strip()


def _resolve_attachment_path(
    image_root: Path, relative_path: str
) -> tuple[str | None, str | None]:
    root_path = image_root.resolve()
    try:
        resolved_path = (root_path / relative_path).resolve()
        resolved_path.relative_to(root_path)
    except (OSError, RuntimeError, ValueError):
        return None, "attachment path escapes the QA directory"

    if not resolved_path.exists():
        return str(resolved_path), "file missing"
    if not resolved_path.is_file():
        return str(resolved_path), "path is not a file"
    return str(resolved_path), None


def _normalize_attachment(
    attachment: Any,
    source_role: str,
    image_root: Path,
) -> JsonRecord:
    if not isinstance(attachment, dict):
        log.warning("invalid attachment in role=%s", source_role)
        return {
            "source_role": source_role,
            "relative_path": None,
            "abs_path": None,
            "exists": False,
            "path_error": "attachment is not an object",
        }

    normalized = dict(attachment)
    normalized["source_role"] = source_role
    relative_path = normalized.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path.strip():
        normalized.update(
            {
                "abs_path": None,
                "exists": False,
                "path_error": "relative_path is missing",
            }
        )
        return normalized

    absolute_path, path_error = _resolve_attachment_path(image_root, relative_path)
    normalized.update(
        {
            "abs_path": absolute_path
            if path_error != "attachment path escapes the QA directory"
            else None,
            "exists": path_error is None,
            "path_error": path_error,
        }
    )
    if path_error:
        log.warning(
            "attachment path error role=%s path=%s error=%s",
            source_role,
            relative_path,
            path_error,
        )
    return normalized


def _collect_images(record: JsonRecord, image_root: Path) -> list[JsonRecord]:
    images: list[JsonRecord] = []
    for source_role, field_name in (
        ("question", "attachments"),
        ("context", "context_attachments"),
    ):
        for attachment in record.get(field_name) or []:
            images.append(_normalize_attachment(attachment, source_role, image_root))

    for answer in record.get("answers") or []:
        if not isinstance(answer, dict):
            log.warning("invalid answer in question_id=%s", record.get("question_id"))
            continue
        for attachment in answer.get("attachments") or []:
            images.append(_normalize_attachment(attachment, "answer", image_root))
    return images


def _build_turns(record: JsonRecord) -> list[JsonRecord]:
    turns: list[JsonRecord] = []
    question_text = (record.get("question_text") or "").strip()
    if question_text:
        turns.append(
            {
                "role": "student",
                "text": question_text,
                "timestamp": record.get("question_time"),
            }
        )

    for answer in record.get("answers") or []:
        if not isinstance(answer, dict):
            continue
        answer_text = (answer.get("answer_text") or "").strip()
        if answer_text:
            turns.append(
                {
                    "role": "agent",
                    "text": answer_text,
                    "timestamp": answer.get("answer_time"),
                    "answered_by": answer.get("answered_by"),
                }
            )
    return turns


def qa_to_cases(
    path: str | Path,
    image_dir: str | Path | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
    case_id_prefix: str | None = None,
) -> list[JsonRecord]:
    source_path = Path(path).expanduser().resolve()
    log.info(
        "parse started source=%s limit=%s offset=%d",
        source_path,
        limit,
        offset,
    )
    image_root = (
        Path(image_dir).expanduser().resolve() if image_dir else source_path.parent
    )
    data = json.loads(source_path.read_text(encoding="utf-8"))
    records = data.get("questions", []) if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError("qa.json questions must be a list")
    if offset < 0 or (limit is not None and limit < 0):
        raise ValueError("offset and limit must be non-negative")

    student = data.get("student") if isinstance(data, dict) else None
    schema_version = data.get("schema_version") if isinstance(data, dict) else None
    review_method = data.get("review_method") if isinstance(data, dict) else None
    cases: list[JsonRecord] = []

    for record_index, record in enumerate(records):
        if record_index < offset:
            continue
        if limit is not None and len(cases) >= limit:
            break
        if not isinstance(record, dict):
            log.error(
                "invalid question record index=%d source=%s", record_index, source_path
            )
            continue

        source_question_id = record.get("question_id")
        if source_question_id is None:
            source_question_id = f"case_{record_index}"
        source_question_id = str(source_question_id)
        case_id = (
            f"{case_id_prefix}__{source_question_id}"
            if case_id_prefix
            else source_question_id
        )
        text = collect_case_text(record)
        if not text:
            log.warning("case %s: empty question text, skipping", case_id)
            continue

        images = _collect_images(record, image_root)
        metadata = {
            "case_id": case_id,
            "source_question_id": source_question_id,
            "source_file": str(source_path),
            "source_record_index": record_index,
            "schema_version": schema_version,
            "review_method": review_method,
            "student": student,
            "chat_type": record.get("chat_type"),
            "chat_title": record.get("chat_title"),
            "question_time": record.get("question_time"),
            "answered": record.get("answered"),
            "closed": record.get("closed"),
            "closure_basis": record.get("closure_basis"),
            "closure_confirmed_by_student": record.get("closure_confirmed_by_student"),
            "closure_message": record.get("closure_message"),
            "kira_tagged": record.get("kira_tagged"),
            "kira_involved": record.get("kira_involved"),
            "question_text_source": record.get("question_text_source"),
            "unresolved_reason": record.get("unresolved_reason"),
            "answer_quality": record.get("answer_quality"),
            "occurrences": _flatten_source(record.get("source")),
            "context": context_block(record),
        }
        cases.append(
            {
                "case_id": case_id,
                "turns": _build_turns(record),
                "meta": metadata,
                "images": images,
            }
        )
    log.info(
        "parsed cases=%d source=%s limit=%s offset=%d",
        len(cases),
        source_path,
        limit,
        offset,
    )
    return cases


def write_jsonl(rows: Iterable[JsonRecord], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for row in rows:
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")
