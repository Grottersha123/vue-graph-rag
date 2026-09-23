from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.auto import tqdm

from . import conf
from .promt import SYSTEM_PROMPT, CaseResult, build_case_message

log = logging.getLogger("telegram_classificator.agent")
MODEL = conf.TEXT_MODEL
JsonRecord = dict[str, Any]


_RESPONSE_PREVIEW_LIMIT = 4000
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|password)\s*[:=]\s*[\"']?[^,\s}\"']+"
)


def _response_preview(raw_content: str | None) -> str | None:
    if not raw_content:
        return None
    redacted = _SECRET_VALUE_PATTERN.sub(r"\1=[REDACTED]", raw_content.strip())
    return redacted[:_RESPONSE_PREVIEW_LIMIT]


class BadJSON(Exception):
    """Raised when the model response is not a valid classifier result."""

    def __init__(self, message: str, raw_content: str | None = None) -> None:
        super().__init__(message)
        self.raw_preview = _response_preview(raw_content)


_CLASSIFICATION_FIELDS = frozenset(
    {
        "category",
        "secondary_categories",
        "intent",
        "urgency",
        "sentiment",
        "resolution",
        "answerability",
        "tags",
    }
)
_REQUIRED_CLASSIFICATION_FIELDS = frozenset(
    {
        "category",
        "intent",
        "urgency",
        "sentiment",
        "resolution",
        "answerability",
    }
)


def _normalize_question_sources(result: JsonRecord) -> JsonRecord:
    questions = result.get("questions")
    if not isinstance(questions, list):
        return result

    normalized_questions: list[Any] = []
    for question in questions:
        if not isinstance(question, dict) or "sources" not in question:
            normalized_questions.append(question)
            continue
        sources = question["sources"]
        if isinstance(sources, list):
            normalized_questions.append(
                {**question, "sources": [str(source) for source in sources]}
            )
        else:
            normalized_questions.append(question)
    return {**result, "questions": normalized_questions}


def _normalize_model_result(parsed: Any) -> JsonRecord:
    if not isinstance(parsed, dict):
        print(parsed)
        raise BadJSON("classifier response must be a JSON object")
    if "classification" in parsed:
        return _normalize_question_sources(parsed)
    if not _REQUIRED_CLASSIFICATION_FIELDS.issubset(parsed):
        return parsed

    classification = {
        key: parsed[key] for key in _CLASSIFICATION_FIELDS if key in parsed
    }
    normalized = {
        key: value for key, value in parsed.items() if key not in _CLASSIFICATION_FIELDS
    }
    normalized["classification"] = classification
    return _normalize_question_sources(normalized)


def _client() -> OpenAI:
    return OpenAI(
        api_key=conf.OPENAI_API_KEY,
        base_url=conf.OPENAI_BASE_URL,
    )


@retry(
    stop=stop_after_attempt(conf.RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(BadJSON),
    reraise=True,
)
def _classify_once(
    client: OpenAI,
    case: JsonRecord,
    image_descriptions: list[JsonRecord] | None,
    model: str,
) -> CaseResult:
    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_case_message(case, image_descriptions),
            },
        ],
        response_format={"type": "json_object"},
    )
    raw_content = response.choices[0].message.content
    if not raw_content:
        raise BadJSON("classifier returned empty content")
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as error:
        raise BadJSON(f"invalid classifier JSON: {error}") from error
    try:
        return CaseResult(**_normalize_model_result(parsed))
    except BadJSON as error:
        error.raw_preview = error.raw_preview or _response_preview(raw_content)
        raise
    except ValidationError as error:
        raise BadJSON(
            f"classifier schema validation failed: {error}",
            raw_content,
        ) from error


def classify_case(
    client: OpenAI,
    case: JsonRecord,
    image_descriptions: list[JsonRecord] | None = None,
    model: str = MODEL,
) -> CaseResult:
    return _classify_once(client, case, image_descriptions, model)


def load_jsonl(path: str | Path) -> Iterable[JsonRecord]:
    with Path(path).open(encoding="utf-8") as input_file:
        for line in input_file:
            if line.strip():
                yield json.loads(line)


def _load_image_descriptions(path: str | Path | None) -> dict[str, list[JsonRecord]]:
    if path is None or not Path(path).exists():
        return {}
    return {str(row["case_id"]): row.get("images", []) for row in load_jsonl(path)}


def _classify_record(
    case: JsonRecord,
    image_descriptions: dict[str, list[JsonRecord]],
    model: str,
    sleep_seconds: float,
) -> JsonRecord:
    case_id = case.get("case_id")
    log.info("case started case=%s model=%s", case_id, model)
    if sleep_seconds:
        time.sleep(sleep_seconds)
    try:
        result = classify_case(
            _client(),
            case,
            image_descriptions.get(str(case_id)),
            model=model,
        )
        row = result.model_dump(exclude_none=True)
        row["case_id"] = case_id
        row["image_descriptions"] = image_descriptions.get(str(case_id), [])
        log.info("case %s ok, questions=%d", case_id, len(row.get("questions", [])))
        return row
    except BadJSON as error:
        log.exception("case %s schema validation failed", case_id)
        if error.raw_preview:
            log.error(
                "case %s model response preview=%s",
                case_id,
                error.raw_preview,
            )
        row = {
            "case_id": case_id,
            "stage": "classification",
            "error_type": "BadJSON",
            "error": str(error)[:1000],
            "error_ts": datetime.now().isoformat(),
        }
        if error.raw_preview:
            row["model_response_preview"] = error.raw_preview
        return row
    except conf.ModelAPIError:
        log.exception("fatal model error case=%s; stopping pipeline", case_id)
        raise
    except OpenAIError as error:
        log.exception("text API error case=%s; stopping pipeline", case_id)
        raise conf.ModelAPIError(
            "text API request failed; inspect OPENAI_BASE_URL and MODEL"
        ) from error
    except Exception as error:
        log.exception("case %s failed", case_id)
        return {
            "case_id": case_id,
            "stage": "classification",
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
            "error_ts": datetime.now().isoformat(),
        }


def _classify_records(
    cases: list[JsonRecord],
    image_descriptions: dict[str, list[JsonRecord]],
    model: str,
    max_workers: int,
    sleep_seconds: float,
) -> list[JsonRecord]:
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    log.info(
        "classification stage started cases=%d workers=%d",
        len(cases),
        max_workers,
    )
    if max_workers == 1:
        results: list[JsonRecord] = []
        with tqdm(total=len(cases), desc="classify", unit="case") as progress:
            for case in cases:
                results.append(
                    _classify_record(
                        case,
                        image_descriptions,
                        model,
                        sleep_seconds,
                    )
                )
                progress.update(1)
        log.info("classification stage finished cases=%d", len(results))
        return results

    results: dict[int, JsonRecord] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[Future[JsonRecord], int] = {
            executor.submit(
                _classify_record,
                case,
                image_descriptions,
                model,
                sleep_seconds,
            ): index
            for index, case in enumerate(cases)
        }
        with tqdm(total=len(cases), desc="classify", unit="case") as progress:
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except conf.ModelAPIError:
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    raise
                except Exception as error:
                    case_id = cases[index].get("case_id")
                    log.exception("classifier worker failed case=%s", case_id)
                    results[index] = {
                        "case_id": case_id,
                        "stage": "classification",
                        "error_type": type(error).__name__,
                        "error": str(error)[:1000],
                        "error_ts": datetime.now().isoformat(),
                    }
                progress.update(1)
    ordered_results = [results[index] for index in range(len(cases))]
    log.info("classification stage finished cases=%d", len(ordered_results))
    return ordered_results


def write_jsonl(rows: Iterable[JsonRecord], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for row in rows:
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(
    cases_path: str | Path,
    out_path: str | Path,
    img_desc_path: str | Path | None = None,
    model: str = MODEL,
    limit: int | None = conf.CASE_LIMIT,
    max_workers: int = conf.MAX_WORKERS,
    sleep: float = 0.0,
    errors_path: str | Path | None = None,
) -> None:
    cases = list(load_jsonl(cases_path))
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        cases = cases[:limit]
    descriptions = _load_image_descriptions(img_desc_path)
    started = datetime.now()
    log.info("start cases=%d model=%s workers=%d", len(cases), model, max_workers)

    results = _classify_records(cases, descriptions, model, max_workers, sleep)
    output_path = Path(out_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for row in tqdm(results, desc="write results", unit="case"):
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")

    if errors_path is not None:
        write_jsonl(
            (row for row in results if row.get("error")),
            errors_path,
        )
    elapsed = (datetime.now() - started).total_seconds()
    error_count = sum(1 for row in results if row.get("error"))
    log.info(
        "done seconds=%.1f cases=%d errors=%d",
        elapsed,
        len(results),
        error_count,
    )
