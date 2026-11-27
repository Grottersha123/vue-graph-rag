from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def required_secret(values: Mapping[str, str], name: str) -> str:
    direct = values.get(name, "").strip()
    file_value = values.get(f"{name}_FILE", "").strip()
    if direct and file_value:
        raise ConfigurationError(f"set only one of {name} or {name}_FILE")
    if file_value:
        path = Path(file_value)
        if path.is_symlink() or not path.is_file():
            raise ConfigurationError(f"{name}_FILE must identify a regular file")
        try:
            direct = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ConfigurationError(f"{name}_FILE could not be read") from error
    if not direct:
        raise ConfigurationError(f"{name} is required")
    return direct


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    return bounded_positive_int(values, name, default, maximum=None)


def bounded_positive_int(
    values: Mapping[str, str], name: str, default: int, maximum: int | None
) -> int:
    try:
        value = int(values.get(name, str(default)))
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value < 1:
        raise ConfigurationError(f"{name} must be positive")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{name} cannot exceed {maximum}")
    return value


def _bounded_positive_float(
    values: Mapping[str, str], name: str, default: float, maximum: float
) -> float:
    try:
        value = float(values.get(name, str(default)))
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric") from error
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise ConfigurationError(
            f"{name} must be finite, positive, and at most {maximum:g}"
        )
    return value


def _allowed_users(values: Mapping[str, str]) -> frozenset[int]:
    raw = _required(values, "TELEGRAM_ALLOWED_USER_IDS")
    try:
        users = frozenset(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as error:
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_USER_IDS must contain integers"
        ) from error
    if not users or any(user < 1 for user in users):
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_USER_IDS must contain positive integers"
        )
    return users


def validate_local_redis_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ConfigurationError("MVP_V2_REDIS_URL must use redis or rediss")
    if parsed.username or parsed.password:
        raise ConfigurationError("MVP_V2_REDIS_URL credentials are not supported")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1", "redis"}:
        raise ConfigurationError(
            "MVP_V2_REDIS_URL must identify the local Redis service"
        )
    return value


def _database_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value) or value in {".", ".."}:
        raise ConfigurationError("MVP_V2_DATABASE_NAME must be a safe name")
    return value


LogLevel = Literal["ERROR", "DEBUG"]


def parse_log_level(values: Mapping[str, str]) -> LogLevel:
    value = values.get("MVP_V2_LOG_LEVEL", "ERROR").strip().upper()
    if value not in {"ERROR", "DEBUG"}:
        raise ConfigurationError("MVP_V2_LOG_LEVEL must be ERROR or DEBUG")
    return cast(LogLevel, value)


def _optional_selora_configuration(
    values: Mapping[str, str],
) -> tuple[str | None, str | None, str | None]:
    url = values.get("MVP_V2_SELORA_URL", "").strip()
    model = values.get("MVP_V2_SELORA_MODEL", "").strip()
    key_is_configured = bool(
        values.get("MVP_V2_SELORA_API_KEY", "").strip()
        or values.get("MVP_V2_SELORA_API_KEY_FILE", "").strip()
    )
    if not url and not model and not key_is_configured:
        return None, None, None
    if not url or not model:
        raise ConfigurationError(
            "MVP_V2_SELORA_URL and MVP_V2_SELORA_MODEL are required with Selora key"
        )
    api_key = required_secret(values, "MVP_V2_SELORA_API_KEY")
    parsed = urlsplit(url)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if (
        (parsed.scheme != "https" and not is_loopback_http)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "MVP_V2_SELORA_URL must be an HTTPS or loopback HTTP origin "
            "without credentials, query, or fragment"
        )
    return url.rstrip("/"), api_key, model


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewaySettings:
    database_name: str
    databases_root: Path
    audit_directory: Path
    redis_url: str
    ollama_base_url: str
    ollama_allowed_lan_hosts: frozenset[str]
    chat_model: str
    chat_model_version: str
    backend_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    total_concurrency: int = 6
    model_concurrency: int = 2
    evidence_limit: int = 8
    minimum_score: float = 0.55
    ollama_timeout_seconds: float = 120.0
    log_level: LogLevel = "ERROR"
    selora_url: str | None = None
    selora_api_key: str | None = field(default=None, repr=False)
    selora_model: str | None = None

    @property
    def database_path(self) -> Path:
        return self.databases_root / f"{self.database_name}.lbdb"

    @classmethod
    def from_environment(
        cls, values: Mapping[str, str] | None = None
    ) -> GatewaySettings:
        source = os.environ if values is None else values
        database_name = _database_name(_required(source, "MVP_V2_DATABASE_NAME"))
        databases_root = Path(source.get("MVP_V2_DATABASES_ROOT", "runtime/databases"))
        audit_directory = Path(
            source.get("MVP_V2_GATEWAY_LOG_DIRECTORY", "runtime/logs/gateway")
        )
        try:
            minimum_score = float(source.get("MVP_V2_MINIMUM_SIMILARITY_SCORE", "0.55"))
        except ValueError as error:
            raise ConfigurationError(
                "MVP_V2_MINIMUM_SIMILARITY_SCORE must be numeric"
            ) from error
        if not -1.0 <= minimum_score <= 1.0:
            raise ConfigurationError(
                "MVP_V2_MINIMUM_SIMILARITY_SCORE must be between -1 and 1"
            )
        total_concurrency = _positive_int(source, "MVP_V2_TOTAL_CONCURRENCY", 6)
        if total_concurrency > 6:
            raise ConfigurationError("MVP_V2_TOTAL_CONCURRENCY cannot exceed 6")
        selora_url, selora_api_key, selora_model = _optional_selora_configuration(
            source
        )
        backend_token = required_secret(source, "MVP_V2_BACKEND_TOKEN")
        if len(backend_token) < 32:
            raise ConfigurationError(
                "MVP_V2_BACKEND_TOKEN must contain at least 32 characters"
            )
        return cls(
            database_name=database_name,
            databases_root=databases_root,
            audit_directory=audit_directory,
            redis_url=validate_local_redis_url(
                source.get("MVP_V2_REDIS_URL", "redis://127.0.0.1:6379/0")
            ),
            ollama_base_url=source.get(
                "MVP_V2_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
            ).strip(),
            ollama_allowed_lan_hosts=frozenset(
                item.strip()
                for item in source.get("MVP_V2_OLLAMA_ALLOWED_LAN_HOSTS", "").split(",")
                if item.strip()
            ),
            chat_model=_required(source, "MVP_V2_CHAT_MODEL"),
            chat_model_version=_required(source, "MVP_V2_CHAT_MODEL_VERSION"),
            backend_token=backend_token,
            allowed_user_ids=_allowed_users(source),
            api_host=source.get("MVP_V2_API_HOST", "127.0.0.1"),
            api_port=_positive_int(source, "MVP_V2_API_PORT", 8000),
            total_concurrency=total_concurrency,
            model_concurrency=_positive_int(source, "MVP_V2_MODEL_CONCURRENCY", 2),
            evidence_limit=bounded_positive_int(
                source, "MVP_V2_EVIDENCE_LIMIT", 8, maximum=8
            ),
            minimum_score=minimum_score,
            ollama_timeout_seconds=_bounded_positive_float(
                source, "MVP_V2_OLLAMA_TIMEOUT_SECONDS", 120.0, 600.0
            ),
            log_level=parse_log_level(source),
            selora_url=selora_url,
            selora_api_key=selora_api_key,
            selora_model=selora_model,
        )
