from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from tests.backend.helpers import FakeChat, MemoryProfileStore, gateway
from vue_rag.application.dto.chat import ChatAnswer, ChatRequest
from vue_rag.application.rag.agent.prompts import (
    DEFAULT_PROMPT_TEMPLATE,
    MCP_PROMPT_TEMPLATES,
    PROMPT_TEMPLATES,
    PromptTemplate,
)
from vue_rag.backend.config import ConfigurationError, GatewaySettings
from vue_rag.backend.contracts import MessageRequest
from vue_rag.backend.profiles import (
    ProfileRegistry,
    ProfileStoreUnavailableError,
)
from vue_rag.backend.service import CapacityLimiter, GatewayOverloadedError
from vue_rag.telegram_bot.config import TelegramSettings


def test_message_contract_requires_conversation_to_equal_chat_id() -> None:
    with pytest.raises(ValidationError, match="conversation_id must equal chat_id"):
        MessageRequest(
            request_id="request-1",
            telegram_user_id=42,
            chat_id=-100,
            source_message_id=7,
            conversation_id="different",
            question="Question",
        )


def test_gateway_settings_fail_fast_without_secret_values() -> None:
    values = {
        "MVP_V2_DATABASE_NAME": "../unsafe",
        "MVP_V2_CHAT_MODEL": "chat",
        "MVP_V2_CHAT_MODEL_VERSION": "v1",
        "TELEGRAM_ALLOWED_USER_IDS": "42",
        "MVP_V2_BACKEND_TOKEN": "x" * 32,
    }
    with pytest.raises(ConfigurationError, match="safe name") as captured:
        GatewaySettings.from_environment(values)
    assert "chat" not in str(captured.value)


def test_gateway_settings_validate_ollama_timeout() -> None:
    values = {
        "MVP_V2_DATABASE_NAME": "database",
        "MVP_V2_CHAT_MODEL": "chat",
        "MVP_V2_CHAT_MODEL_VERSION": "v1",
        "TELEGRAM_ALLOWED_USER_IDS": "42",
        "MVP_V2_BACKEND_TOKEN": "x" * 32,
    }

    assert GatewaySettings.from_environment(values).ollama_timeout_seconds == 120.0
    values["MVP_V2_OLLAMA_TIMEOUT_SECONDS"] = "45.5"
    assert GatewaySettings.from_environment(values).ollama_timeout_seconds == 45.5


@pytest.mark.parametrize(
    "timeout",
    ["invalid", "nan", "inf", "0", "-1", "600.1"],
)
def test_gateway_settings_reject_invalid_ollama_timeout(timeout: str) -> None:
    values = {
        "MVP_V2_DATABASE_NAME": "database",
        "MVP_V2_CHAT_MODEL": "chat",
        "MVP_V2_CHAT_MODEL_VERSION": "v1",
        "TELEGRAM_ALLOWED_USER_IDS": "42",
        "MVP_V2_BACKEND_TOKEN": "x" * 32,
        "MVP_V2_OLLAMA_TIMEOUT_SECONDS": timeout,
    }

    with pytest.raises(ConfigurationError, match="MVP_V2_OLLAMA_TIMEOUT_SECONDS"):
        GatewaySettings.from_environment(values)


def test_settings_fail_fast_when_required_values_are_missing() -> None:
    with pytest.raises(ConfigurationError, match="MVP_V2_CHAT_MODEL"):
        GatewaySettings.from_environment(
            {
                "MVP_V2_DATABASE_NAME": "database",
                "MVP_V2_CHAT_MODEL_VERSION": "v1",
                "TELEGRAM_ALLOWED_USER_IDS": "42",
                "MVP_V2_BACKEND_TOKEN": "x" * 32,
            }
        )
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        TelegramSettings.from_environment({"TELEGRAM_ALLOWED_USER_IDS": "42"})


def test_settings_read_secret_files_without_exposing_values(tmp_path: Path) -> None:
    backend_secret = tmp_path / "gateway_token"
    bot_secret = tmp_path / "telegram_bot_token"
    backend_secret.write_text("x" * 32, encoding="utf-8")
    bot_secret.write_text("123456:abcdefghijklmnopqrstuvwxyzABCDEFGH", encoding="utf-8")
    gateway_settings = GatewaySettings.from_environment(
        {
            "MVP_V2_DATABASE_NAME": "database",
            "MVP_V2_CHAT_MODEL": "chat",
            "MVP_V2_CHAT_MODEL_VERSION": "v1",
            "TELEGRAM_ALLOWED_USER_IDS": "42",
            "MVP_V2_BACKEND_TOKEN_FILE": str(backend_secret),
        }
    )
    telegram_settings = TelegramSettings.from_environment(
        {
            "TELEGRAM_BOT_TOKEN_FILE": str(bot_secret),
            "MVP_V2_BACKEND_TOKEN_FILE": str(backend_secret),
            "TELEGRAM_ALLOWED_USER_IDS": "42",
        }
    )

    assert gateway_settings.backend_token == "x" * 32
    assert telegram_settings.bot_token.startswith("123456:")
    assert "x" * 32 not in repr(gateway_settings)
    assert telegram_settings.bot_token not in repr(telegram_settings)


def test_profile_registry_uses_chat_templates_not_mcp_templates() -> None:
    registry = ProfileRegistry()

    assert registry.default_id == "support-v1"
    assert tuple(item.name for item in registry.all()) == tuple(PROMPT_TEMPLATES)
    assert not set(MCP_PROMPT_TEMPLATES).intersection(
        item.name for item in registry.all()
    )


def test_profile_registry_reflects_a_new_template_without_transport_changes() -> None:
    second = PromptTemplate(
        name="second-v1",
        system_prompt="system",
        answer_policy="{allowed} {insufficient}",
        citation_retry_notice="{allowed} {insufficient}",
    )
    registry = ProfileRegistry(
        {DEFAULT_PROMPT_TEMPLATE.name: DEFAULT_PROMPT_TEMPLATE, second.name: second},
        DEFAULT_PROMPT_TEMPLATE,
    )

    assert tuple(item.name for item in registry.all()) == ("second-v1", "support-v1")


def test_profile_resolution_precedence_is_explicit_stored_then_default() -> None:
    registry = ProfileRegistry()
    store = MemoryProfileStore()

    assert registry.resolve("support-v1", 42, store).name == "support-v1"
    store.values[42] = "support-v1"
    assert registry.resolve(None, 42, store).name == "support-v1"
    store.values.clear()
    assert registry.resolve(None, 42, store).name == "support-v1"


class _UnavailableProfileStore:
    def read(self, telegram_user_id: int) -> str | None:
        raise ProfileStoreUnavailableError("unavailable")

    def write(self, telegram_user_id: int, profile_id: str) -> None:
        raise ProfileStoreUnavailableError("unavailable")

    def ready(self) -> bool:
        return False


def test_profile_resolution_falls_back_to_global_default_when_redis_is_down() -> None:
    registry = ProfileRegistry()

    resolved = registry.resolve(None, 42, _UnavailableProfileStore())

    assert resolved.name == "support-v1"


def test_capacity_limiter_rejects_without_waiting_per_model() -> None:
    limiter = CapacityLimiter(2, {"model-a": 1, "model-b": 1})

    with limiter.acquire("model-a"):
        with pytest.raises(GatewayOverloadedError, match="model capacity"):
            with limiter.acquire("model-a"):
                pass
        with limiter.acquire("model-b"):
            pass


def test_capacity_limiter_rejects_seventh_global_request() -> None:
    limiter = CapacityLimiter(6, {"chat": 7})

    with ExitStack() as stack:
        for _ in range(6):
            stack.enter_context(limiter.acquire("chat"))
        with pytest.raises(GatewayOverloadedError, match="gateway capacity"):
            with limiter.acquire("chat"):
                pass


class _ConcurrentChat(FakeChat):
    def __init__(self, barrier: Barrier) -> None:
        super().__init__()
        self._barrier = barrier

    def answer(self, request: ChatRequest) -> ChatAnswer:
        self._barrier.wait(timeout=2.0)
        return super().answer(request)


def test_gateway_allows_two_configured_model_calls_to_run_concurrently(
    tmp_path: Path,
) -> None:
    chat = _ConcurrentChat(Barrier(2))
    service, _, _, _ = gateway(
        tmp_path,
        chat=chat,
        capacity=CapacityLimiter(6, {"chat": 2}),
    )
    requests = tuple(
        MessageRequest(
            request_id=f"request-{index}",
            telegram_user_id=42,
            chat_id=-100,
            source_message_id=index + 1,
            conversation_id="-100",
            question="Question",
        )
        for index in range(2)
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = tuple(executor.map(service.handle, requests))

    assert {response.request_id for response in responses} == {
        "request-0",
        "request-1",
    }
