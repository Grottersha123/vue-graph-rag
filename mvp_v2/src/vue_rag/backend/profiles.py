from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import redis

from vue_rag.application.rag.agent.prompts import (
    DEFAULT_PROMPT_TEMPLATE,
    PROMPT_TEMPLATES,
    PromptTemplate,
)


class ProfileStore(Protocol):
    def read(self, telegram_user_id: int) -> str | None: ...

    def write(self, telegram_user_id: int, profile_id: str) -> None: ...

    def ready(self) -> bool: ...


class ProfileStoreUnavailableError(RuntimeError):
    pass


class RedisProfileStore:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @staticmethod
    def profile_key(telegram_user_id: int) -> str:
        if telegram_user_id < 1:
            raise ValueError("telegram_user_id must be positive")
        return f"mvp-v2:prompt-profile:{telegram_user_id}"

    def read(self, telegram_user_id: int) -> str | None:
        try:
            payload = self._client.get(self.profile_key(telegram_user_id))
        except redis.RedisError as error:
            raise ProfileStoreUnavailableError(
                "profile storage is unavailable"
            ) from error
        if payload is None:
            return None
        if isinstance(payload, bytes):
            return payload.decode("utf-8")
        return str(payload)

    def write(self, telegram_user_id: int, profile_id: str) -> None:
        try:
            self._client.set(self.profile_key(telegram_user_id), profile_id)
        except redis.RedisError as error:
            raise ProfileStoreUnavailableError(
                "profile storage is unavailable"
            ) from error

    def ready(self) -> bool:
        try:
            return bool(self._client.ping())
        except redis.RedisError:
            return False


class ProfileRegistry:
    def __init__(
        self,
        templates: Mapping[str, PromptTemplate] = PROMPT_TEMPLATES,
        default: PromptTemplate = DEFAULT_PROMPT_TEMPLATE,
    ) -> None:
        if default.name not in templates:
            raise ValueError("default prompt profile is not registered")
        self._templates = templates
        self._default = default

    @property
    def default_id(self) -> str:
        return self._default.name

    def all(self) -> tuple[PromptTemplate, ...]:
        return tuple(self._templates[key] for key in sorted(self._templates))

    def require(self, profile_id: str) -> PromptTemplate:
        try:
            return self._templates[profile_id]
        except KeyError as error:
            raise ValueError("unknown prompt profile") from error

    def resolve(
        self,
        explicit_profile_id: str | None,
        telegram_user_id: int,
        store: ProfileStore,
    ) -> PromptTemplate:
        if explicit_profile_id is not None:
            return self.require(explicit_profile_id)
        try:
            stored = store.read(telegram_user_id)
        except ProfileStoreUnavailableError:
            return self._default
        return self._default if stored is None else self.require(stored)
