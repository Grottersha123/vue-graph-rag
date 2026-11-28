from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from aiogram.types import Message

from vue_rag.backend.audit import TelegramAudit
from vue_rag.telegram_bot.app import TelegramController
from vue_rag.telegram_bot.client import HttpBackendClient
from vue_rag.telegram_bot.state import RedisFeedbackState


class FailingBackend:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"backend must not be used by /start: {name}")


class UnusedFeedbackState:
    async def read(self, telegram_user_id: int) -> None:
        raise AssertionError("feedback state must not be used by /start")


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=-100)
        self.message_id = 7
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


def controller(tmp_path: Path, allowed_user_ids: frozenset[int]) -> TelegramController:
    audit = TelegramAudit(tmp_path)
    audit.prepare()
    return TelegramController(
        backend=cast(HttpBackendClient, FailingBackend()),
        state=cast(RedisFeedbackState, UnusedFeedbackState()),
        audit=audit,
        allowed_user_ids=allowed_user_ids,
    )


def actions(tmp_path: Path) -> tuple[str, ...]:
    events = (
        json.loads(line)
        for line in (tmp_path / "telegram.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    return tuple(str(event["action"]) for event in events)


def test_start_returns_help_without_backend_or_feedback_calls(tmp_path: Path) -> None:
    async def scenario() -> None:
        message = FakeMessage(42)

        await controller(tmp_path, frozenset({42})).start(cast(Message, message))

        assert message.answers == [
            "Здравствуйте! Отправьте текстовый вопрос. "
            "Используйте /profiles, чтобы выбрать профиль."
        ]
        assert actions(tmp_path) == ("start_received", "start_sent")

    asyncio.run(scenario())


def test_start_denies_user_without_backend_call(tmp_path: Path) -> None:
    async def scenario() -> None:
        message = FakeMessage(7)

        await controller(tmp_path, frozenset({42})).start(cast(Message, message))

        assert message.answers == ["Доступ запрещён."]
        assert actions(tmp_path) == ("start_received", "allowlist_rejected")

    asyncio.run(scenario())
