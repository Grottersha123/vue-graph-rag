from __future__ import annotations

import asyncio

from vue_rag.telegram_bot.state import PendingFeedback, RedisFeedbackState


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, ttl_seconds: int, value: str) -> bool:
        self.values[key] = value
        self.ttls[key] = ttl_seconds
        return True

    async def delete(self, key: str) -> int:
        self.ttls.pop(key, None)
        return int(self.values.pop(key, None) is not None)


def test_pending_feedback_uses_namespaced_key_bounded_ttl_and_delete() -> None:
    async def scenario() -> None:
        redis = MemoryRedis()
        state = RedisFeedbackState(redis, ttl_seconds=300)
        pending = PendingFeedback(
            request_id="request-1",
            telegram_user_id=42,
            conversation_id="-100",
            rating=5,
        )

        await state.write(pending)

        key = "mvp-v2:pending-feedback:42"
        assert redis.ttls == {key: 300}
        assert await state.read(42) == pending
        assert "rendered_prompt" not in redis.values[key]

        await state.delete(42)
        assert await state.read(42) is None

    asyncio.run(scenario())
