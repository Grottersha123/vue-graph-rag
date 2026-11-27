from __future__ import annotations

import logging
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
from vue_rag.telegram_bot.state import ConversationState, RedisFeedbackState

_LOGGER = logging.getLogger("vue_rag.telegram.controller")


class ActiveConversationState:
    async def mark_active(self, telegram_user_id: int) -> bool:
        return False

    async def claim_profile_selection(
        self, telegram_user_id: int, profile_id: str
    ) -> bool:
        return True

    async def release_profile_selection(
        self, telegram_user_id: int, profile_id: str
    ) -> None:
        return None


class TelegramController:
    def __init__(
        self,
        *,
        backend: HttpBackendClient,
        state: RedisFeedbackState,
        audit: TelegramAudit,
        allowed_user_ids: frozenset[int],
        conversation_state: ConversationState | None = None,
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
        self._conversation_state = conversation_state or ActiveConversationState()

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
        try:
            await self._conversation_state.mark_active(sender.id)
            await message.answer(
                "Здравствуйте! Отправьте текстовый вопрос. "
                "Используйте /profiles, чтобы выбрать профиль."
            )
        except (TelegramAPIError, redis.RedisError) as error:
            self._events.error(
                request_id=request_id,
                conversation_id=str(message.chat.id),
                operation="start",
                error_code="service_unavailable",
                exception=error,
            )
            await message.answer("Сервис временно недоступен. Попробуйте позже.")
            return
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
        _LOGGER.debug("message received request_id=%s", request_id)
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
            resumed_after_inactivity = await self._conversation_state.mark_active(
                sender.id
            )
            if resumed_after_inactivity:
                await message.answer(
                    "С возвращением! Я обработаю ваш новый вопрос. "
                    "Профиль можно изменить командой /profiles."
                )
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
            if response.fallback_used:
                _LOGGER.debug(
                    "fallback notification request_id=%s provider=%s",
                    request_id,
                    response.provider,
                )
                await message.answer(
                    "Основная модель временно недоступна. "
                    "Ответ подготовлен резервной моделью."
                )
                self._events.transport(
                    status="sent",
                    request_id=request_id,
                    user_id=sender.id,
                    chat_id=message.chat.id,
                    source_message_id=message.message_id,
                    action="fallback_notified",
                    delivery_status="sent",
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
            _LOGGER.error(
                "message failed request_id=%s error_type=%s",
                request_id,
                type(error).__name__,
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
            claimed = await self._conversation_state.claim_profile_selection(
                sender.id, profile_id
            )
        except redis.RedisError as error:
            self._events.error(
                request_id=request_id,
                conversation_id=str(chat_id),
                operation="profile_update",
                error_code="service_unavailable",
                exception=error,
            )
            await callback.answer("Не удалось изменить профиль.", show_alert=True)
            return
        if not claimed:
            await callback.answer("Профиль уже выбран.")
            return
        try:
            await self._backend.select_profile(sender.id, profile_id)
        except (TelegramGatewayError, ValueError) as error:
            await self._conversation_state.release_profile_selection(
                sender.id, profile_id
            )
            self._events.error(
                request_id=request_id,
                conversation_id=str(chat_id),
                operation="profile_update",
                error_code="service_unavailable",
                exception=error,
            )
            await callback.answer("Не удалось изменить профиль.", show_alert=True)
            return
        try:
            message = callback.message
            if isinstance(message, Message):
                await message.edit_text(
                    f"Профиль «{profile_id}» выбран. Теперь отправьте вопрос.",
                    reply_markup=None,
                )
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
        except TelegramAPIError as error:
            self._events.error(
                request_id=request_id,
                conversation_id=str(chat_id),
                operation="profile_confirmation",
                error_code="delivery_failed",
                exception=error,
            )


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
