from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH)


class ModelAPIError(RuntimeError):
    """Fatal model endpoint error that must stop the pipeline."""


def _resolve_path(key: str, default: str) -> Path:
    value = Path(os.getenv(key, default)).expanduser()
    if value.is_absolute():
        return value
    return PROJECT_ROOT / value


def _read_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer") from error


DATA_DIR = _resolve_path("DATA_DIR", "data")
LOG_DIR = _resolve_path("LOG_DIR", "logs")
IMAGE_DIR = _resolve_path("IMAGE_DIR", ".")
QA_JSON = _resolve_path("QA_JSON", "qa.json")

CASES = DATA_DIR / "cases.jsonl"
RESULTS = DATA_DIR / "results.jsonl"
IMG_DESCS = DATA_DIR / "image_descriptions.jsonl"
LOG_PATH = LOG_DIR / "pipeline.log"

TEXT_MODEL = os.getenv("MODEL", "gpt-4o-mini")
VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "qwen2.5vl:7b")
VISION_BASE_URL = os.getenv("LOCAL_VISION_BASE_URL") or None
VISION_API_KEY = os.getenv("LOCAL_VISION_API_KEY", "dummy")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "dummy")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None

IMG_DESC_SENTENCES = _read_int("IMG_DESC_SENTENCES", 5)
MAX_IMAGES_PER_CASE = _read_int("MAX_IMAGES_PER_CASE", 4)
RETRY_ATTEMPTS = max(1, _read_int("RETRY_ATTEMPTS", 3))
CASE_LIMIT = _read_int("CASE_LIMIT", 10)
MAX_WORKERS = max(1, _read_int("MAX_WORKERS", 4))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def configure_logging() -> None:
    ensure_dirs()
    logger = logging.getLogger("telegram_classificator")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False

    if any(
        getattr(handler, "_classifier_handler", False) for handler in logger.handlers
    ):
        return

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler._classifier_handler = True
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler._classifier_handler = True
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)


def summary() -> dict[str, str | int]:
    """Return notebook-safe configuration without secret values."""
    return {
        "PROJECT_ROOT": str(PROJECT_ROOT),
        "QA_JSON": str(QA_JSON),
        "IMAGE_DIR": str(IMAGE_DIR),
        "DATA_DIR": str(DATA_DIR),
        "TEXT_MODEL": TEXT_MODEL,
        "VISION_MODEL": VISION_MODEL,
        "VISION_BASE_URL": VISION_BASE_URL or "openai-default",
        "OPENAI_API_KEY": "set" if os.getenv("OPENAI_API_KEY") else "empty",
        "IMG_DESC_SENTENCES": IMG_DESC_SENTENCES,
        "CASE_LIMIT": CASE_LIMIT,
        "MAX_WORKERS": MAX_WORKERS,
        "LOG_LEVEL": LOG_LEVEL,
    }
