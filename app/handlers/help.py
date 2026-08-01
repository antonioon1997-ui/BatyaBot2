from __future__ import annotations

import json
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.domain import department_by_role
from app.keyboards.help import (
    faq_article_keyboard,
    faq_group_keyboard,
    faq_main_keyboard,
    help_input_cancel_keyboard,
    help_main_keyboard,
    help_settings_keyboard,
    message_style_keyboard,
)
from app.keyboards.feedback import admin_feedback_card_keyboard
from app.presentation.faq import FAQ_GROUPS
from app.services.feedback import create_feedback
from app.services.preferences import get_message_style, set_message_style, user_text
from app.services.ui_messages import delete_trigger_message, send_ui_text
from app.services.ui_versions import help_settings_enabled
from app.services.users import get_user_by_telegram_id
from app.states import HelpStates
from app.utils import html_escape
from app.version import get_version

router = Router()

UPDATE_HISTORY_FILE = Path(__file__).resolve().parents[2] / "runtime" / "update_history.json"

FALLBACK_23_NOTES = [
    "Добавлен раздел «Помощь» с короткими инструкциями по тикетам и заказам.",
    "Через «Есть идея?» можно одним сообщением отправить вопрос, ошибку или предложение.",
    "В настройках можно выбрать строгий или дружелюбный стиль коротких сообщений.",
    "Администратор может проводить компактные голосования и возвращать предыдущую версию интерфейса.",
]


def _active_user(user):
    return user and int(user["is_active"] or 0) == 1


async def _get_active_user(target) -> object | None:
    user = await get_user_by_telegram_id(target.from_user.id)
    return user if _active_user(user) else None


async def _deny(target) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer("Нет доступа.", show_alert=True)
    else:
        await target.answer("Нет доступа.")


async def _send_help_screen(target, text: str, reply_markup=None) -> None:
    await send_ui_text(
        target.bot,
        chat_id=target.from_user.id,
        text=text,
        reply_markup=reply_markup,
    )


async def _send_help(target, state: FSMContext | None = None) -> None:
    user = await _get_active_user(target)
    if not user:
        await _deny(target)
        return
    if state is not None:
        await state.clear()
    await _send_help_screen(
        target,
        "❓ <b>Помощь</b>\n\n"
        "Здесь можно быстро найти инструкцию, посмотреть изменения или написать, что стоит сделать удобнее.",
        reply_markup=help_main_keyboard(),
    )
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.message(F.text == "❓ Помощь")
async def bottom_help(message: Message, state: FSMContext):
    await _send_help(message, state)
    await delete_trigger_message(message)


@router.callback_query(F.data == "help_main")
async def help_main_callback(call: CallbackQuery, state: FSMContext):
    await _send_help(call, state)


@router.callback_query(F.data == "help_faq")
async def help_faq_callback(call: CallbackQuery):
    if not await _get_active_user(call):
        await _deny(call)
        return
    await _send_help_screen(
        call,
        "📖 <b>Как сделать...</b>\n\nВыберите, с чем связан вопрос.",
        reply_markup=faq_main_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("help_faq_group:"))
async def help_faq_group_callback(call: CallbackQuery):
    if not await _get_active_user(call):
        await _deny(call)
        return
    group = call.data.split(":", 1)[1]
    if group not in FAQ_GROUPS:
        await call.answer("Раздел не найден.", show_alert=True)
        return
    title = "🎫 Работа с тикетами" if group == "tickets" else "📦 Работа с заказами"
    await _send_help_screen(
        call,
        f"<b>{title}</b>\n\nВыберите вопрос.",
        reply_markup=faq_group_keyboard(group),
    )
    await call.answer()


@router.callback_query(F.data.startswith("help_faq_article:"))
async def help_faq_article_callback(call: CallbackQuery):
    if not await _get_active_user(call):
        await _deny(call)
        return
    parts = call.data.split(":", 2)
    if len(parts) != 3:
        await call.answer("Статья не найдена.", show_alert=True)
        return
    _, group, article_id = parts
    article = FAQ_GROUPS.get(group, {}).get(article_id)
    if not article:
        await call.answer("Статья не найдена.", show_alert=True)
        return
    title, body = article
    await _send_help_screen(
        call,
        f"<b>{html_escape(title)}</b>\n\n{html_escape(body)}",
        reply_markup=faq_article_keyboard(group),
    )
    await call.answer()


def _notes_for_user(notes: list[str], role: str | None, *, is_admin_user: bool = False) -> list[str]:
    department = department_by_role(role)
    result: list[str] = []
    for note in notes:
        item = str(note).strip()
        lowered = item.lower()
        if lowered.startswith("[admin]"):
            if is_admin_user:
                result.append(item[len("[admin]"):].strip())
            continue
        if lowered.startswith("[client]"):
            if department == "client":
                result.append(item[len("[client]"):].strip())
            continue
        if lowered.startswith("[purchasing]"):
            if department == "purchasing":
                result.append(item[len("[purchasing]"):].strip())
            continue
        if lowered.startswith("[all]"):
            result.append(item[len("[all]"):].strip())
            continue
        result.append(item)
    return [item for item in result if item]


def _latest_update() -> tuple[str, list[str]]:
    try:
        payload = json.loads(UPDATE_HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, list) and payload:
            latest = payload[-1]
            version = str(latest.get("version") or get_version())
            changes = [str(item).strip() for item in latest.get("changes", []) if str(item).strip()]
            return version, changes
    except (OSError, json.JSONDecodeError):
        pass
    return get_version(), list(FALLBACK_23_NOTES)


@router.callback_query(F.data == "help_whats_new")
async def help_whats_new_callback(call: CallbackQuery):
    user = await _get_active_user(call)
    if not user:
        await _deny(call)
        return
    version, notes = _latest_update()
    notes = _notes_for_user(notes, user["role"], is_admin_user=int(call.from_user.id) == int(settings.admin_id))
    lines = "\n".join(f"• {html_escape(item)}" for item in notes) or "• Пользовательских изменений для вашей роли нет."
    await _send_help_screen(
        call,
        "🆕 <b>Что нового</b>\n\n"
        f"Текущая версия: <b>{html_escape(version)}</b>\n\n"
        f"{lines}",
        reply_markup=help_main_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "help_feedback")
async def help_feedback_callback(call: CallbackQuery, state: FSMContext):
    if not await _get_active_user(call):
        await _deny(call)
        return
    await state.clear()
    await state.set_state(HelpStates.waiting_feedback)
    await _send_help_screen(
        call,
        "💬 <b>Есть идея?</b>\n\n"
        "Если появилась мысль, как сделать вашу работу проще или удобнее — напишите её одним сообщением.\n\n"
        "Сюда же можно отправить вопрос, сообщить об ошибке, пожаловаться на неудобную функцию "
        "или предложить что-то новое. Можно приложить скриншот, фото или документ.",
        reply_markup=help_input_cancel_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "help_question")
async def help_question_callback(call: CallbackQuery, state: FSMContext):
    if not await _get_active_user(call):
        await _deny(call)
        return
    await state.clear()
    await state.set_state(HelpStates.waiting_question)
    await _send_help_screen(
        call,
        "❓ <b>Не нашли ответ?</b>\n\n"
        "Напишите вопрос своими словами. Он напрямую придёт администратору. "
        "Можно приложить скриншот, фото или документ.",
        reply_markup=help_input_cancel_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "help_input_cancel")
async def help_input_cancel_callback(call: CallbackQuery, state: FSMContext):
    await _send_help(call, state)


@router.message(Command("cancel"), HelpStates.waiting_feedback)
@router.message(Command("cancel"), HelpStates.waiting_question)
async def help_cancel_command(message: Message, state: FSMContext):
    await _send_help(message, state)


def _message_attachment(message: Message) -> tuple[str | None, str | None, str | None]:
    if message.photo:
        return message.photo[-1].file_id, "photo", None
    if message.document:
        return message.document.file_id, "document", message.document.file_name
    if message.video:
        return message.video.file_id, "video", message.video.file_name
    if message.audio:
        return message.audio.file_id, "audio", message.audio.file_name
    if message.voice:
        return message.voice.file_id, "voice", None
    return None, None, None


async def _save_user_message(message: Message, state: FSMContext, bot: Bot, source: str) -> None:
    user = await _get_active_user(message)
    if not user:
        await state.clear()
        await _deny(message)
        return

    text = (message.text or message.caption or "").strip()
    file_id, file_type, file_name = _message_attachment(message)
    if not text and not file_id:
        await _send_help_screen(
            message,
            "Отправьте текст, фото, видео, голосовое сообщение или документ.",
            reply_markup=help_input_cancel_keyboard(),
        )
        return

    feedback_id = await create_feedback(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        role=user["role"],
        source=source,
        text=text or None,
        file_id=file_id,
        file_type=file_type,
        file_name=file_name,
    )

    source_title = "Вопрос из раздела помощи" if source == "question" else "Сообщение из «Есть идея?»"
    body = html_escape(text) if text else "<i>Текст не указан — приложен файл.</i>"
    await bot.send_message(
        settings.admin_id,
        f"💬 <b>{source_title}</b>\n\n"
        f"Номер: <b>#{feedback_id}</b>\n"
        f"От: {html_escape(message.from_user.full_name)}\n"
        f"Username: @{html_escape(message.from_user.username, default='нет')}\n"
        f"Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Роль: {html_escape(user['role'])}\n\n"
        f"{body}",
        reply_markup=admin_feedback_card_keyboard(feedback_id, "new", "new"),
    )
    if file_id:
        try:
            await bot.copy_message(
                chat_id=settings.admin_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except Exception:
            await bot.send_message(
                settings.admin_id,
                f"Не удалось автоматически скопировать вложение. File ID: <code>{html_escape(file_id)}</code>",
            )

    await state.clear()
    key = "question_saved" if source == "question" else "feedback_saved"
    await _send_help_screen(
        message,
        await user_text(message.from_user.id, key),
        reply_markup=help_main_keyboard(),
    )


@router.message(HelpStates.waiting_feedback)
async def process_feedback(message: Message, state: FSMContext, bot: Bot):
    await _save_user_message(message, state, bot, "idea")


@router.message(HelpStates.waiting_question)
async def process_question(message: Message, state: FSMContext, bot: Bot):
    await _save_user_message(message, state, bot, "question")


@router.callback_query(F.data == "help_settings")
async def help_settings_callback(call: CallbackQuery):
    if not await _get_active_user(call):
        await _deny(call)
        return
    if not help_settings_enabled():
        await call.answer("Настройки скрыты в выбранной версии интерфейса.", show_alert=True)
        return
    await _send_help_screen(
        call,
        "⚙️ <b>Настройки</b>\n\n"
        "Пока здесь можно изменить только стиль коротких системных сообщений.",
        reply_markup=help_settings_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "help_message_style")
async def help_message_style_callback(call: CallbackQuery):
    if not await _get_active_user(call):
        await _deny(call)
        return
    if not help_settings_enabled():
        await call.answer("Настройки скрыты в выбранной версии интерфейса.", show_alert=True)
        return
    style = await get_message_style(call.from_user.id)
    await _send_help_screen(
        call,
        "💬 <b>Стиль сообщений</b>\n\n"
        "Меняет короткие системные фразы и сообщения о пустых списках. "
        "Рабочие данные, статусы и логика бота остаются одинаковыми.\n\n"
        "📋 <b>Строгий:</b> «Тикетов нет.»\n"
        "🙂 <b>Дружелюбный:</b> «Пока всё спокойно — подходящих тикетов нет 😊»",
        reply_markup=message_style_keyboard(style),
    )
    await call.answer()


@router.callback_query(F.data.startswith("help_style:"))
async def help_style_callback(call: CallbackQuery):
    if not await _get_active_user(call):
        await _deny(call)
        return
    style = call.data.split(":", 1)[1]
    selected = await set_message_style(call.from_user.id, style)
    key = "style_saved_friendly" if selected == "friendly" else "style_saved_strict"
    await _send_help_screen(
        call,
        await user_text(call.from_user.id, key),
        reply_markup=message_style_keyboard(selected),
    )
    await call.answer("Настройка сохранена")
