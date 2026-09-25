from __future__ import annotations

from typing import Protocol

from mvp_v2.domain.base import ContractModel


class PendingFeedback(ContractModel):
    request_id: str
    telegram_user_id: int
    conversation_id: str
    rating: int


class FeedbackState(Protocol):
    async def read(self, telegram_user_id: int) -> PendingFeedback | None: ...

    async def write(self, value: PendingFeedback) -> None: ...

    async def delete(self, telegram_user_id: int) -> None: ...


class FeedbackRedis(Protocol):
    async def get(self, key: str) -> bytes | str | None: ...

    async def setex(self, key: str, ttl_seconds: int, value: str) -> object: ...

    async def delete(self, key: str) -> object: ...


class RedisFeedbackState:
    def __init__(self, client: FeedbackRedis, ttl_seconds: int) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def key(telegram_user_id: int) -> str:
        return f"mvp-v2:pending-feedback:{telegram_user_id}"

    async def read(self, telegram_user_id: int) -> PendingFeedback | None:
        payload = await self._client.get(self.key(telegram_user_id))
        return None if payload is None else PendingFeedback.model_validate_json(payload)

    async def write(self, value: PendingFeedback) -> None:
        await self._client.setex(
            self.key(value.telegram_user_id),
            self._ttl_seconds,
            value.model_dump_json(),
        )

    async def delete(self, telegram_user_id: int) -> None:
        await self._client.delete(self.key(telegram_user_id))
