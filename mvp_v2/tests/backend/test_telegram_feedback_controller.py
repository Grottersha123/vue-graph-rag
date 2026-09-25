from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
from aiogram.types import CallbackQuery

from mvp_v2.backend.audit import TelegramAudit
from mvp_v2.telegram_bot.audit import TelegramEventRecorder
from mvp_v2.telegram_bot.client import HttpBackendClient
from mvp_v2.telegram_bot.feedback import TelegramFeedbackController
from mvp_v2.telegram_bot.state import PendingFeedback, RedisFeedbackState


class MemoryFeedbackState:
    def __init__(self, pending: PendingFeedback | None) -> None:
        self.pending = pending
        self.reads = 0
        self.deletes = 0

    async def read(self, telegram_user_id: int) -> PendingFeedback | None:
        self.reads += 1
        return self.pending

    async def write(self, value: PendingFeedback) -> None:
        self.pending = value

    async def delete(self, telegram_user_id: int) -> None:
        self.deletes += 1
        self.pending = None


class FakeBackend:
    def __init__(self) -> None:
        self.feedback_calls = 0

    async def submit_feedback(self, request: object) -> None:
        self.feedback_calls += 1


class FakeCallback:
    def __init__(self, user_id: int, chat_id: int = -100) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=7,
        )
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def test_feedback_callbacks_enforce_allowlist_and_match_pending_request(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        pending = PendingFeedback(
            request_id="new-request",
            telegram_user_id=42,
            conversation_id="-100",
            rating=5,
        )
        state = MemoryFeedbackState(pending)
        backend = FakeBackend()
        audit = TelegramAudit(tmp_path)
        audit.prepare()
        controller = TelegramFeedbackController(
            backend=cast(HttpBackendClient, backend),
            state=cast(RedisFeedbackState, state),
            events=TelegramEventRecorder(audit),
            allowed_user_ids=frozenset({42}),
        )
        unauthorized = cast(CallbackQuery, FakeCallback(7))
        stale = cast(CallbackQuery, FakeCallback(42))
        matching = cast(CallbackQuery, FakeCallback(42))

        await controller.submit_without_comment(unauthorized, "new-request")
        assert state.reads == 0
        assert backend.feedback_calls == 0

        await controller.cancel_feedback(stale, "old-request")
        assert state.pending == pending
        assert state.deletes == 0

        await controller.cancel_feedback(matching, "new-request")
        assert state.pending is None
        assert state.deletes == 1

    asyncio.run(scenario())


def test_http_backend_client_sends_service_authorization() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer " + "x" * 32
        return httpx.Response(
            200,
            json={
                "profiles": [
                    {
                        "profile_id": "support-v1",
                        "display_name": "support-v1",
                        "is_default": True,
                    }
                ],
                "default_profile_id": "support-v1",
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = HttpBackendClient(client, "http://backend:8000", "x" * 32)
            profiles = await backend.profiles()
            assert profiles.default_profile_id == "support-v1"

    asyncio.run(scenario())
