from __future__ import annotations

from typing import Literal
from uuid import uuid4

from aiogram.types import CallbackQuery

from mvp_v2.backend.audit import (
    ErrorEvent,
    TelegramAudit,
    TelegramEvent,
    event_envelope,
    exception_diagnostics,
)


class TelegramEventRecorder:
    def __init__(self, audit: TelegramAudit) -> None:
        self._audit = audit

    def transport(
        self,
        *,
        status: Literal["received", "sent", "failed", "skipped"],
        request_id: str,
        user_id: int,
        chat_id: int,
        source_message_id: int | None,
        action: str,
        delivery_status: str,
        related_request_id: str | None = None,
    ) -> None:
        self._audit.telegram_writer.append(
            TelegramEvent.model_validate(
                {
                    **event_envelope(
                        component="telegram",
                        event_type="transport",
                        request_id=request_id,
                        conversation_id=str(chat_id),
                        status=status,
                    ),
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "source_message_id": source_message_id,
                    "action": action,
                    "delivery_status": delivery_status,
                    "related_request_id": related_request_id,
                }
            )
        )

    def error(
        self,
        *,
        request_id: str,
        conversation_id: str,
        operation: str,
        error_code: str,
        exception: BaseException | None = None,
    ) -> None:
        error, formatted_traceback = exception_diagnostics(exception)
        self._audit.error_writer.append(
            ErrorEvent.model_validate(
                {
                    **event_envelope(
                        component="telegram",
                        event_type="error",
                        request_id=request_id,
                        conversation_id=conversation_id,
                        status="error",
                    ),
                    "operation": operation,
                    "error_code": error_code,
                    "message": "The Telegram operation could not be completed.",
                    "error": error,
                    "traceback": formatted_traceback,
                }
            )
        )

    @staticmethod
    def callback_coordinates(callback: CallbackQuery) -> tuple[int, int | None]:
        message = callback.message
        if message is None:
            return callback.from_user.id, None
        return message.chat.id, message.message_id

    def begin_callback(
        self,
        callback: CallbackQuery,
        *,
        action: str,
        related_request_id: str | None = None,
    ) -> tuple[str, int, int | None]:
        request_id = uuid4().hex
        chat_id, source_message_id = self.callback_coordinates(callback)
        self.transport(
            status="received",
            request_id=request_id,
            user_id=callback.from_user.id,
            chat_id=chat_id,
            source_message_id=source_message_id,
            action=action,
            delivery_status="received",
            related_request_id=related_request_id,
        )
        return request_id, chat_id, source_message_id
