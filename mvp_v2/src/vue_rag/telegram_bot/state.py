from __future__ import annotations

from typing import Protocol

from vue_rag.domain.base import ContractModel


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


class ConversationState(Protocol):
    async def mark_active(self, telegram_user_id: int) -> bool: ...

    async def claim_profile_selection(
        self, telegram_user_id: int, profile_id: str
    ) -> bool: ...

    async def release_profile_selection(
        self, telegram_user_id: int, profile_id: str
    ) -> None: ...


class ConversationRedis(Protocol):
    async def get(self, key: str) -> bytes | str | None: ...

    async def setex(self, key: str, ttl_seconds: int, value: str) -> object: ...

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool | None: ...

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


class RedisConversationState:
    def __init__(self, client: ConversationRedis, inactivity_seconds: int) -> None:
        self._client = client
        self._inactivity_seconds = inactivity_seconds

    @staticmethod
    def activity_key(telegram_user_id: int) -> str:
        return f"mvp-v2:telegram-activity:{telegram_user_id}"

    @staticmethod
    def profile_key(telegram_user_id: int, profile_id: str) -> str:
        return f"mvp-v2:profile-selection:{telegram_user_id}:{profile_id}"

    async def mark_active(self, telegram_user_id: int) -> bool:
        key = self.activity_key(telegram_user_id)
        was_inactive = await self._client.get(key) is None
        await self._client.setex(key, self._inactivity_seconds, "active")
        return was_inactive

    async def claim_profile_selection(
        self, telegram_user_id: int, profile_id: str
    ) -> bool:
        claimed = await self._client.set(
            self.profile_key(telegram_user_id, profile_id),
            "selected",
            ex=self._inactivity_seconds,
            nx=True,
        )
        return bool(claimed)

    async def release_profile_selection(
        self, telegram_user_id: int, profile_id: str
    ) -> None:
        await self._client.delete(self.profile_key(telegram_user_id, profile_id))
