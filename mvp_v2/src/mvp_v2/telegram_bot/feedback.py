from __future__ import annotations

import redis
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from mvp_v2.backend.contracts import FeedbackRequest
from mvp_v2.telegram_bot.audit import TelegramEventRecorder
from mvp_v2.telegram_bot.client import HttpBackendClient, TelegramGatewayError
from mvp_v2.telegram_bot.keyboards import comment_keyboard
from mvp_v2.telegram_bot.state import PendingFeedback, RedisFeedbackState


class TelegramFeedbackController:
    def __init__(
        self,
        *,
        backend: HttpBackendClient,
        state: RedisFeedbackState,
        events: TelegramEventRecorder,
        allowed_user_ids: frozenset[int],
    ) -> None:
        self._backend = backend
        self._state = state
        self._events = events
        self._allowed_user_ids = allowed_user_ids

    async def read(self, telegram_user_id: int) -> PendingFeedback | None:
        return await self._state.read(telegram_user_id)

    async def delete(self, telegram_user_id: int) -> None:
        await self._state.delete(telegram_user_id)

    async def rate(self, callback: CallbackQuery, request_id: str, rating: int) -> None:
        update_id, chat_id, source_message_id = self._events.begin_callback(
            callback,
            action="feedback_rating_received",
            related_request_id=request_id,
        )
        message = callback.message
        if message is None or callback.from_user.id not in self._allowed_user_ids:
            await callback.answer("Доступ запрещён.", show_alert=True)
            self._events.transport(
                status="skipped",
                request_id=update_id,
                user_id=callback.from_user.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
                related_request_id=request_id,
            )
            return
        try:
            pending = PendingFeedback(
                request_id=request_id,
                telegram_user_id=callback.from_user.id,
                conversation_id=str(message.chat.id),
                rating=rating,
            )
            await self._state.write(pending)
            await callback.answer("Оценка сохранена")
            await message.answer(
                "Напишите необязательный комментарий или пропустите его.",
                reply_markup=comment_keyboard(request_id),
            )
            self._events.transport(
                status="sent",
                request_id=update_id,
                user_id=callback.from_user.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="feedback_comment_requested",
                delivery_status="sent",
                related_request_id=request_id,
            )
        except (TelegramAPIError, redis.RedisError, ValueError) as error:
            self._events.error(
                request_id=update_id,
                conversation_id=str(chat_id),
                operation="feedback_rating",
                error_code="service_unavailable",
                exception=error,
            )
            await callback.answer("Не удалось сохранить оценку.", show_alert=True)

    async def submit_without_comment(
        self, callback: CallbackQuery, request_id: str
    ) -> None:
        update_id, chat_id, source_message_id = self._events.begin_callback(
            callback,
            action="feedback_submit_received",
            related_request_id=request_id,
        )
        if callback.from_user.id not in self._allowed_user_ids:
            await callback.answer("Доступ запрещён.", show_alert=True)
            self._events.transport(
                status="skipped",
                request_id=update_id,
                user_id=callback.from_user.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
                related_request_id=request_id,
            )
            return
        try:
            pending = await self._state.read(callback.from_user.id)
            if (
                pending is None
                or pending.request_id != request_id
                or pending.conversation_id != str(chat_id)
            ):
                await callback.answer("Оценка устарела.", show_alert=True)
                self._events.transport(
                    status="skipped",
                    request_id=update_id,
                    user_id=callback.from_user.id,
                    chat_id=chat_id,
                    source_message_id=source_message_id,
                    action="feedback_expired",
                    delivery_status="skipped",
                    related_request_id=request_id,
                )
                return
            await self.submit(pending, None)
            await self._state.delete(callback.from_user.id)
            await callback.answer("Спасибо за оценку")
            self._events.transport(
                status="sent",
                request_id=update_id,
                user_id=callback.from_user.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="feedback_sent",
                delivery_status="sent",
                related_request_id=request_id,
            )
        except (
            TelegramGatewayError,
            TelegramAPIError,
            redis.RedisError,
            ValueError,
        ) as error:
            self._events.error(
                request_id=update_id,
                conversation_id=str(chat_id),
                operation="feedback_submit",
                error_code="service_unavailable",
                exception=error,
            )
            await callback.answer("Не удалось отправить оценку.", show_alert=True)

    async def submit(self, pending: PendingFeedback, comment: str | None) -> None:
        await self._backend.submit_feedback(
            FeedbackRequest(
                request_id=pending.request_id,
                telegram_user_id=pending.telegram_user_id,
                conversation_id=pending.conversation_id,
                rating=pending.rating,
                comment=comment,
            )
        )

    async def cancel_feedback(
        self, callback: CallbackQuery, related_request_id: str
    ) -> None:
        update_id, chat_id, source_message_id = self._events.begin_callback(
            callback,
            action="feedback_comment_cancelled",
            related_request_id=related_request_id,
        )
        if callback.from_user.id not in self._allowed_user_ids:
            await callback.answer("Доступ запрещён.", show_alert=True)
            self._events.transport(
                status="skipped",
                request_id=update_id,
                user_id=callback.from_user.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
                related_request_id=related_request_id,
            )
            return
        try:
            pending = await self._state.read(callback.from_user.id)
            if (
                pending is None
                or pending.request_id != related_request_id
                or pending.conversation_id != str(chat_id)
            ):
                await callback.answer("Оценка устарела.", show_alert=True)
                return
            await self._state.delete(callback.from_user.id)
            await callback.answer("Комментарий отменён")
            self._events.transport(
                status="sent",
                request_id=update_id,
                user_id=callback.from_user.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="feedback_cancelled",
                delivery_status="sent",
                related_request_id=related_request_id,
            )
        except (TelegramAPIError, redis.RedisError, ValueError) as error:
            self._events.error(
                request_id=update_id,
                conversation_id=str(chat_id),
                operation="feedback_cancel",
                error_code="service_unavailable",
                exception=error,
            )
            await callback.answer("Не удалось отменить комментарий.", show_alert=True)

    async def skip_feedback(
        self, callback: CallbackQuery, related_request_id: str
    ) -> None:
        update_id, chat_id, source_message_id = self._events.begin_callback(
            callback,
            action="feedback_skipped",
            related_request_id=related_request_id,
        )
        if callback.from_user.id not in self._allowed_user_ids:
            await callback.answer("Доступ запрещён.", show_alert=True)
            self._events.transport(
                status="skipped",
                request_id=update_id,
                user_id=callback.from_user.id,
                chat_id=chat_id,
                source_message_id=source_message_id,
                action="allowlist_rejected",
                delivery_status="skipped",
                related_request_id=related_request_id,
            )
            return
        await callback.answer("Оценка пропущена")
        self._events.transport(
            status="sent",
            request_id=update_id,
            user_id=callback.from_user.id,
            chat_id=chat_id,
            source_message_id=source_message_id,
            action="feedback_skip_acknowledged",
            delivery_status="sent",
            related_request_id=related_request_id,
        )
