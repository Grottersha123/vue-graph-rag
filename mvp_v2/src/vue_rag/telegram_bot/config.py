from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from vue_rag.backend.config import (
    ConfigurationError,
    LogLevel,
    bounded_positive_int,
    parse_log_level,
    required_secret,
    validate_local_redis_url,
)


def _allowed_users(values: Mapping[str, str]) -> frozenset[int]:
    raw = values.get("TELEGRAM_ALLOWED_USER_IDS", "")
    try:
        identities = frozenset(
            int(item.strip()) for item in raw.split(",") if item.strip()
        )
    except ValueError as error:
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_USER_IDS must contain integers"
        ) from error
    if not identities or any(identity < 1 for identity in identities):
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_USER_IDS must contain positive integers"
        )
    return identities


def _backend_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
        "backend",
    }:
        raise ConfigurationError("MVP_V2_BACKEND_URL must identify the local backend")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True, kw_only=True)
class TelegramSettings:
    bot_token: str = field(repr=False)
    backend_token: str = field(repr=False)
    backend_url: str
    redis_url: str
    audit_directory: Path
    allowed_user_ids: frozenset[int]
    request_timeout_seconds: float = 60.0
    feedback_ttl_seconds: int = 900
    session_inactivity_seconds: int = 3_600
    log_level: LogLevel = "ERROR"

    @classmethod
    def from_environment(
        cls, values: Mapping[str, str] | None = None
    ) -> TelegramSettings:
        source = os.environ if values is None else values
        token = required_secret(source, "TELEGRAM_BOT_TOKEN")
        backend_token = required_secret(source, "MVP_V2_BACKEND_TOKEN")
        if len(backend_token) < 32:
            raise ConfigurationError(
                "MVP_V2_BACKEND_TOKEN must contain at least 32 characters"
            )
        feedback_ttl = bounded_positive_int(
            source, "MVP_V2_FEEDBACK_TTL_SECONDS", 900, maximum=86_400
        )
        session_inactivity = bounded_positive_int(
            source,
            "MVP_V2_SESSION_INACTIVITY_SECONDS",
            3_600,
            maximum=2_592_000,
        )
        return cls(
            bot_token=token,
            backend_token=backend_token,
            backend_url=_backend_url(
                source.get("MVP_V2_BACKEND_URL", "http://127.0.0.1:8000")
            ),
            redis_url=validate_local_redis_url(
                source.get("MVP_V2_REDIS_URL", "redis://127.0.0.1:6379/1")
            ),
            audit_directory=Path(
                source.get("MVP_V2_GATEWAY_LOG_DIRECTORY", "runtime/logs/gateway")
            ),
            allowed_user_ids=_allowed_users(source),
            feedback_ttl_seconds=feedback_ttl,
            session_inactivity_seconds=session_inactivity,
            log_level=parse_log_level(source),
        )
