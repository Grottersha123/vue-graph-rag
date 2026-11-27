from __future__ import annotations

import asyncio
import logging
from typing import cast

import httpx
import redis.asyncio as redis
from aiogram import Bot

from vue_rag.backend.audit import TelegramAudit
from vue_rag.runtime_logging import configure_runtime_logging
from vue_rag.telegram_bot.app import TelegramController, build_dispatcher
from vue_rag.telegram_bot.client import HttpBackendClient
from vue_rag.telegram_bot.config import TelegramSettings
from vue_rag.telegram_bot.state import (
    ConversationRedis,
    FeedbackRedis,
    RedisConversationState,
    RedisFeedbackState,
)


async def run_polling(settings: TelegramSettings) -> None:
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    redis_client = redis.Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )
    audit = TelegramAudit(settings.audit_directory)
    audit.prepare()
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        controller = TelegramController(
            backend=HttpBackendClient(
                client, settings.backend_url, settings.backend_token
            ),
            state=RedisFeedbackState(
                cast(FeedbackRedis, redis_client), settings.feedback_ttl_seconds
            ),
            audit=audit,
            allowed_user_ids=settings.allowed_user_ids,
            conversation_state=RedisConversationState(
                cast(ConversationRedis, redis_client),
                settings.session_inactivity_seconds,
            ),
        )
        bot = Bot(token=settings.bot_token)
        try:
            await build_dispatcher(controller).start_polling(bot)
        finally:
            await bot.session.close()
            await redis_client.aclose()


def main() -> None:
    settings = TelegramSettings.from_environment()
    configure_runtime_logging(
        settings.log_level,
        component="vue_rag.telegram",
        secrets=(settings.bot_token, settings.backend_token),
    )
    logging.getLogger("vue_rag.telegram").debug("telegram polling starting")
    asyncio.run(run_polling(settings))
