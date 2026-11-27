from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def feedback_keyboard(request_id: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=str(rating), callback_data=f"rate:{request_id}:{rating}"
        )
        for rating in range(1, 6)
    ]
    buttons.append(
        InlineKeyboardButton(text="Пропустить", callback_data=f"skip:{request_id}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def comment_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без комментария", callback_data=f"comment-skip:{request_id}"
                ),
                InlineKeyboardButton(
                    text="Отменить", callback_data=f"comment-cancel:{request_id}"
                ),
            ]
        ]
    )


def profile_keyboard(profile_ids: tuple[str, ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=item, callback_data=f"profile:{item}")]
            for item in profile_ids
        ]
    )
