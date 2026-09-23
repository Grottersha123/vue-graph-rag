from __future__ import annotations

import base64
import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.auto import tqdm

from . import conf

log = logging.getLogger("telegram_classificator.image_describer")
ImageRecord = dict[str, Any]
DescriptionResult = tuple[str | None, str | None]


def _client(model_base_url: str | None = None) -> OpenAI:
    return OpenAI(
        api_key=conf.VISION_API_KEY,
        base_url=model_base_url or conf.VISION_BASE_URL,
    )


def _data_uri(abs_path: str) -> str:
    extension = Path(abs_path).suffix.lower().lstrip(".") or "jpeg"
    encoded = base64.b64encode(Path(abs_path).read_bytes()).decode()
    return f"data:image/{extension};base64,{encoded}"


def _build_prompt(sentences: int) -> str:
    return (
        f"Опиши изображение ровно {sentences} предложениями на русском. "
        "Что за интерфейс или документ, какой контекст, и что именно "
        "проблемного или важного видно. Если это инструкция, скриншот "
        "инструкции или другой материал, обязательно назови, что именно "
        "на фотографии, какой это экран или документ и о чём инструкция. "
        "Без вступлений, только описание."
    )


@retry(
    stop=stop_after_attempt(conf.RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(ValueError),
    reraise=True,
)
def _request_description(
    client: OpenAI,
    model: str,
    prompt: str,
    data_uri: str,
) -> str:
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("local vision model returned empty content")
    return content.strip()


def _cache_key(image: ImageRecord) -> str:
    absolute_path = image.get("abs_path")
    if isinstance(absolute_path, str) and absolute_path:
        return absolute_path
    return f"missing:{image.get('relative_path')}:{image.get('path_error')}"


def _describe_one(
    image: ImageRecord,
    model: str,
    base_url: str | None,
    sentences: int,
) -> DescriptionResult:
    relative_path = image.get("relative_path")
    started_at = time.monotonic()
    log.info("image description started path=%s", relative_path)

    if not image.get("exists"):
        error = image.get("path_error") or "file missing"
        log.warning("image description skipped path=%s reason=%s", relative_path, error)
        return None, error

    absolute_path = image.get("abs_path")
    if not isinstance(absolute_path, str) or not absolute_path:
        error = "absolute image path is missing"
        log.warning("image description skipped path=%s reason=%s", relative_path, error)
        return None, error

    try:
        data_uri = _data_uri(absolute_path)
    except (OSError, ValueError) as error:
        log.exception("image read failed path=%s", relative_path)
        log.info(
            "image description finished path=%s status=error seconds=%.1f",
            relative_path,
            time.monotonic() - started_at,
        )
        return None, str(error)[:300]

    try:
        description = _request_description(
            _client(base_url),
            model,
            _build_prompt(sentences),
            data_uri,
        )
    except OpenAIError as error:
        log.exception(
            "vision API error path=%s; stopping pipeline",
            relative_path,
        )
        log.info(
            "image description finished path=%s status=api_error seconds=%.1f",
            relative_path,
            time.monotonic() - started_at,
        )
        raise conf.ModelAPIError(
            "vision API request failed; inspect LOCAL_VISION_BASE_URL and "
            "LOCAL_VISION_MODEL"
        ) from error
    except Exception as error:
        log.exception("image description failed path=%s", relative_path)
        log.info(
            "image description finished path=%s status=error seconds=%.1f",
            relative_path,
            time.monotonic() - started_at,
        )
        return None, str(error)[:300]

    log.info(
        "image description finished path=%s status=ok seconds=%.1f",
        relative_path,
        time.monotonic() - started_at,
    )
    return description, None


def _describe_unique(
    images: list[ImageRecord],
    cache: dict[str, DescriptionResult],
    model: str,
    base_url: str | None,
    sentences: int,
    max_workers: int,
) -> None:
    unique_images: dict[str, ImageRecord] = {}
    for image in images:
        key = _cache_key(image)
        if key not in cache:
            unique_images.setdefault(key, image)

    for key, image in unique_images.items():
        if not image.get("exists"):
            cache[key] = _describe_one(image, model, base_url, sentences)

    pending_images = {
        key: image for key, image in unique_images.items() if image.get("exists")
    }
    if not pending_images:
        log.info(
            "image stage finished occurrences=%d unique=%d pending=0",
            len(images),
            len(unique_images),
        )
        return

    total = len(pending_images)
    log.info(
        "image stage started occurrences=%d unique=%d pending=%d workers=%d",
        len(images),
        len(unique_images),
        total,
        max_workers,
    )
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[Future[DescriptionResult], tuple[str, ImageRecord]] = {
            executor.submit(_describe_one, image, model, base_url, sentences): (
                key,
                image,
            )
            for key, image in pending_images.items()
        }
        with tqdm(total=total, desc="describe images", unit="image") as progress:
            for future in as_completed(futures):
                key, image = futures[future]
                try:
                    cache[key] = future.result()
                except conf.ModelAPIError:
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    raise
                except Exception as error:
                    log.exception("worker failed image_key=%s", key)
                    cache[key] = (None, str(error)[:300])
                completed += 1
                description, error = cache[key]
                progress.update(1)
                log.info(
                    "image stage progress completed=%d/%d path=%s status=%s",
                    completed,
                    total,
                    image.get("relative_path"),
                    "ok" if description and not error else "error",
                )

    log.info("image stage finished completed=%d/%d", completed, total)


def _attach_descriptions(
    images: list[ImageRecord],
    cache: dict[str, DescriptionResult],
) -> list[ImageRecord]:
    described: list[ImageRecord] = []
    for image in images:
        description, error = cache[_cache_key(image)]
        described.append(
            {
                **image,
                "detailed_description": description,
                "desc_error": error,
            }
        )
    return described


def describe_images(
    images: list[ImageRecord],
    model: str | None = None,
    base_url: str | None = None,
    sentences: int = 5,
    max_workers: int = 4,
) -> list[ImageRecord]:
    """Describe image occurrences while preserving their original metadata."""
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    selected_model = model or conf.VISION_MODEL
    selected_base_url = base_url or conf.VISION_BASE_URL
    cache: dict[str, DescriptionResult] = {}
    _describe_unique(
        images,
        cache,
        selected_model,
        selected_base_url,
        sentences,
        max_workers,
    )
    return _attach_descriptions(images, cache)


def describe_case_images(
    cases: list[ImageRecord],
    model: str | None = None,
    base_url: str | None = None,
    sentences: int = 5,
    max_workers: int = 4,
) -> dict[str, list[ImageRecord]]:
    """Describe all case image occurrences with a shared physical-file cache."""
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    selected_model = model or conf.VISION_MODEL
    selected_base_url = base_url or conf.VISION_BASE_URL
    all_images = [image for case in cases for image in case.get("images", [])]
    cache: dict[str, DescriptionResult] = {}
    _describe_unique(
        all_images,
        cache,
        selected_model,
        selected_base_url,
        sentences,
        max_workers,
    )

    descriptions: dict[str, list[ImageRecord]] = {}
    for case in cases:
        case_id = str(case.get("case_id", "case_unknown"))
        descriptions[case_id] = _attach_descriptions(case.get("images", []), cache)
    return descriptions


def write_descriptions(
    result: dict[str, list[ImageRecord]],
    path: str | Path,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for case_id, images in result.items():
            output_file.write(
                json.dumps(
                    {"case_id": case_id, "images": images},
                    ensure_ascii=False,
                )
                + "\n"
            )


def write_errors(
    result: dict[str, list[ImageRecord]],
    path: str | Path,
) -> None:
    errors: list[ImageRecord] = []
    for case_id, images in result.items():
        for image in images:
            if not image.get("desc_error"):
                continue
            errors.append(
                {
                    "case_id": case_id,
                    "stage": "image_description",
                    "error_type": "DescriptionError",
                    "relative_path": image.get("relative_path"),
                    "source_role": image.get("source_role"),
                    "desc_error": image.get("desc_error"),
                }
            )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for error in errors:
            output_file.write(json.dumps(error, ensure_ascii=False) + "\n")
