from __future__ import annotations

from uuid import uuid4

import redis
from aiogram import Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from vue_rag.backend.audit import TelegramAudit
from vue_rag.backend.contracts import MessageRequest
from vue_rag.telegram_bot.audit import TelegramEventRecorder
from vue_rag.telegram_bot.client import HttpBackendClient, TelegramGatewayError
from vue_rag.telegram_bot.feedback import TelegramFeedbackController
from vue_rag.telegram_bot.keyboards import feedback_keyboard, profile_keyboard
from vue_rag.telegram_bot.state import RedisFeedbackState


class TelegramController:
    def __init__(
        self,
        *,
        backend: HttpBackendClient,
        state: RedisFeedbackState,
        audit: TelegramAudit,
        allowed_user_ids: frozenset[int],
    ) -> None:
        self._backend = backend
        self._events = TelegramEventRecorder(audit)
        self._feedback = TelegramFeedbackController(
            backend=backend,
            state=state,
            events=self._events,
            allowed_user_ids=allowed_user_ids,
        )
        self._allowed_user_ids = allowed_user_ids

    @property
    def feedback(self) -> TelegramFeedbackController:
        return self._feedback

    async def start(self, message: Message) -> None:
        sender = message.from_user
        if sender is None:
            return
        request_id = uuid4().hex
        self._events.transport(
            status="received",
            request_id=request_id,
            user_id=sender.id,
            chat_id=message.chat.id,
            source_message_id=message.message_id,
            action="start_received",
            delivery_status="received",
        )
        if sender.id not in self._allowed_user_ids:
            await message.answer("Доступ запрещён.")
            self._events.transport(
                status="skipped",
                request_id=request_id,
                user_id=sender.id,
                chat_id=message.chat.id,
                source_message_id=message.message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
            )
            return
        await message.answer(
            "Отправьте текстовый вопрос. Используйте /profiles, чтобы выбрать профиль."
        )
        self._events.transport(
            status="sent",
            request_id=request_id,
            user_id=sender.id,
            chat_id=message.chat.id,
            source_message_id=message.message_id,
            action="start_sent",
            delivery_status="sent",
        )

    async def question(self, message: Message) -> None:
        sender = message.from_user
        if sender is None:
            return
        request_id = uuid4().hex
        self._events.transport(
            status="received",
            request_id=request_id,
            user_id=sender.id,
            chat_id=message.chat.id,
            source_message_id=message.message_id,
            action="message_received",
            delivery_status="received",
        )
        if sender.id not in self._allowed_user_ids:
            await message.answer("Доступ запрещён.")
            self._events.transport(
                status="skipped",
                request_id=request_id,
                user_id=sender.id,
                chat_id=message.chat.id,
                source_message_id=message.message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
            )
            return
        if message.text is None:
            await message.answer("Поддерживаются только текстовые сообщения.")
            self._events.transport(
                status="skipped",
                request_id=request_id,
                user_id=sender.id,
                chat_id=message.chat.id,
                source_message_id=message.message_id,
                action="non_text_rejected",
                delivery_status="skipped",
            )
            return
        try:
            pending = await self._feedback.read(sender.id)
            if pending is not None:
                await self._feedback.submit(pending, message.text)
                await self._feedback.delete(sender.id)
                await message.answer("Спасибо за комментарий.")
                self._events.transport(
                    status="sent",
                    request_id=request_id,
                    user_id=sender.id,
                    chat_id=message.chat.id,
                    source_message_id=message.message_id,
                    action="feedback_comment_sent",
                    delivery_status="sent",
                    related_request_id=pending.request_id,
                )
                return
            response = await self._backend.send_message(
                MessageRequest(
                    request_id=request_id,
                    telegram_user_id=sender.id,
                    chat_id=message.chat.id,
                    source_message_id=message.message_id,
                    conversation_id=str(message.chat.id),
                    question=message.text,
                )
            )
            await message.answer(
                response.answer,
                reply_markup=feedback_keyboard(response.request_id),
            )
            self._events.transport(
                status="sent",
                request_id=request_id,
                user_id=sender.id,
                chat_id=message.chat.id,
                source_message_id=message.message_id,
                action="answer_sent",
                delivery_status="sent",
            )
        except (
            TelegramGatewayError,
            TelegramAPIError,
            redis.RedisError,
            ValueError,
        ) as error:
            self._events.error(
                request_id=request_id,
                conversation_id=str(message.chat.id),
                operation="message",
                error_code="service_unavailable",
                exception=error,
            )
            await message.answer("Сервис временно недоступен. Попробуйте позже.")

    async def profiles(self, message: Message) -> None:
        sender = message.from_user
        if sender is None:
            return
        request_id = uuid4().hex
        self._events.transport(
            status="received",
            request_id=request_id,
            user_id=sender.id,
            chat_id=message.chat.id,
            source_message_id=message.message_id,
            action="profiles_requested",
            delivery_status="received",
        )
        if sender.id not in self._allowed_user_ids:
            await message.answer("Доступ запрещён.")
            self._events.transport(
                status="skipped",
                request_id=request_id,
                user_id=sender.id,
                chat_id=message.chat.id,
                source_message_id=message.message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
            )
            return
        try:
            profiles = await self._backend.profiles()
            identifiers = tuple(item.profile_id for item in profiles.profiles)
            await message.answer(
                "Выберите профиль ответа:",
                reply_markup=profile_keyboard(identifiers),
            )
            self._events.transport(
                status="sent",
                request_id=request_id,
                user_id=sender.id,
                chat_id=message.chat.id,
                source_message_id=message.message_id,
                action="profiles_sent",
                delivery_status="sent",
            )
        except (TelegramGatewayError, TelegramAPIError) as error:
            self._events.error(
                request_id=request_id,
                conversation_id=str(message.chat.id),
                operation="profiles",
                error_code="service_unavailable",
                exception=error,
            )
            await message.answer("Выбор профиля временно недоступен.")

    async def select_profile(self, callback: CallbackQuery, profile_id: str) -> None:
        request_id, chat_id, source_message_id = self._events.begin_callback(
            callback, action="profile_selected"
        )
        sender = callback.from_user
        if sender.id not in self._allowed_user_ids:
            await callback.answer("Доступ запрещён.", show_alert=True)
            self._events.transport(
                status="skipped",
                request_id=request_id,
                user_id=sender.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
            )
            return
        try:
            await self._backend.select_profile(sender.id, profile_id)
            await callback.answer(f"Профиль: {profile_id}")
            self._events.transport(
                status="sent",
                request_id=request_id,
                user_id=sender.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="profile_updated",
                delivery_status="sent",
            )
        except (TelegramGatewayError, TelegramAPIError) as error:
            self._events.error(
                request_id=request_id,
                conversation_id=str(chat_id),
                operation="profile_update",
                error_code="service_unavailable",
                exception=error,
            )
            await callback.answer("Не удалось изменить профиль.", show_alert=True)


def build_dispatcher(controller: TelegramController) -> Dispatcher:
    dispatcher = Dispatcher()
    router = Router(name="local-rag-gateway")

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await controller.start(message)

    @router.message(Command("profiles"))
    async def profiles(message: Message) -> None:
        await controller.profiles(message)

    @router.callback_query(F.data.startswith("profile:"))
    async def profile(callback: CallbackQuery) -> None:
        data = callback.data or ""
        await controller.select_profile(callback, data.removeprefix("profile:"))

    @router.callback_query(F.data.startswith("rate:"))
    async def rate(callback: CallbackQuery) -> None:
        _, request_id, rating = (callback.data or "").split(":", 2)
        await controller.feedback.rate(callback, request_id, int(rating))

    @router.callback_query(F.data.startswith("comment-skip:"))
    async def comment_skip(callback: CallbackQuery) -> None:
        await controller.feedback.submit_without_comment(
            callback, (callback.data or "").removeprefix("comment-skip:")
        )

    @router.callback_query(F.data.startswith("comment-cancel:"))
    async def comment_cancel(callback: CallbackQuery) -> None:
        await controller.feedback.cancel_feedback(
            callback, (callback.data or "").removeprefix("comment-cancel:")
        )

    @router.callback_query(F.data.startswith("skip:"))
    async def skip(callback: CallbackQuery) -> None:
        await controller.feedback.skip_feedback(
            callback, (callback.data or "").removeprefix("skip:")
        )

    @router.message()
    async def question(message: Message) -> None:
        await controller.question(message)

    dispatcher.include_router(router)
    return dispatcher
