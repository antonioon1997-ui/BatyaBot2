from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonCommands

logger = logging.getLogger(__name__)


async def configure_bot_command_menu(bot: Bot) -> bool:
    """Настраивает независимую от ReplyKeyboard кнопку меню Telegram.

    Эта кнопка открывает список команд рядом с полем ввода. Она не заменяет
    нижнюю клавиатуру, но даёт постоянный резервный путь к /menu и /start,
    даже если конкретный клиент Telegram вручную свернул ReplyKeyboard.
    """
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="menu", description="Показать главное меню"),
                BotCommand(command="start", description="Обновить меню и начать заново"),
            ]
        )
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        return True
    except Exception:
        # Временная ошибка Telegram при старте не должна останавливать самого бота.
        logger.exception("Не удалось настроить системную кнопку меню Telegram")
        return False
