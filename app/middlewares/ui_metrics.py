from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.services.ui_metrics import (
    classify_callback_button,
    classify_reply_button,
    record_ui_event,
)

logger = logging.getLogger(__name__)


def _clicked_button_text(call: CallbackQuery) -> str | None:
    message = call.message
    markup = getattr(message, "reply_markup", None) if message else None
    rows = getattr(markup, "inline_keyboard", None)
    if not rows:
        return None
    for row in rows:
        for button in row:
            if getattr(button, "callback_data", None) == call.data:
                return getattr(button, "text", None)
    return None


class UiMetricsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            info = None
            source = None
            user = getattr(event, "from_user", None)
            if isinstance(event, Message):
                info = classify_reply_button(event.text)
                source = "reply"
            elif isinstance(event, CallbackQuery):
                info = classify_callback_button(event.data, _clicked_button_text(event))
                source = "inline"

            if info and source and user:
                button_id, button_text, scope = info
                await record_ui_event(
                    user_id=int(user.id),
                    button_id=button_id,
                    button_text=button_text,
                    source=source,
                    scope=scope,
                )
        except Exception:
            # Аналитика не должна мешать основной работе бота даже при временной ошибке БД.
            logger.exception("Не удалось записать метрику нажатия")

        return await handler(event, data)
