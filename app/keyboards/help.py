from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.presentation.faq import FAQ_GROUPS
from app.services.ui_versions import help_settings_enabled


def help_main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📖 Как сделать...", callback_data="help_faq")],
        [InlineKeyboardButton(text="🆕 Что нового", callback_data="help_whats_new")],
        [InlineKeyboardButton(text="💬 Есть идея?", callback_data="help_feedback")],
    ]
    if help_settings_enabled():
        rows.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="help_settings")])
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faq_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎫 Работа с тикетами", callback_data="help_faq_group:tickets")],
            [InlineKeyboardButton(text="📦 Работа с заказами", callback_data="help_faq_group:orders")],
            [InlineKeyboardButton(text="❓ Не нашли ответ?", callback_data="help_question")],
            [InlineKeyboardButton(text="⬅️ Помощь", callback_data="help_main")],
        ]
    )


def faq_group_keyboard(group: str) -> InlineKeyboardMarkup:
    articles = FAQ_GROUPS.get(group, {})
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"help_faq_article:{group}:{article_id}")]
        for article_id, (title, _) in articles.items()
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="❓ Не нашли ответ?", callback_data="help_question")],
            [InlineKeyboardButton(text="⬅️ Как сделать...", callback_data="help_faq")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faq_article_keyboard(group: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❓ Не нашли ответ?", callback_data="help_question")],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"help_faq_group:{group}")],
        ]
    )


def help_input_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="help_input_cancel")],
        ]
    )


def help_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Стиль сообщений", callback_data="help_message_style")],
            [InlineKeyboardButton(text="⬅️ Помощь", callback_data="help_main")],
        ]
    )


def message_style_keyboard(current_style: str) -> InlineKeyboardMarkup:
    strict_prefix = "✅ " if current_style == "strict" else ""
    friendly_prefix = "✅ " if current_style == "friendly" else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{strict_prefix}📋 Строгий", callback_data="help_style:strict")],
            [InlineKeyboardButton(text=f"{friendly_prefix}🙂 Дружелюбный", callback_data="help_style:friendly")],
            [InlineKeyboardButton(text="⬅️ Настройки", callback_data="help_settings")],
        ]
    )
