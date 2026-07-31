from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.domain import department_by_role
from app.keyboards.feedback import (
    CHOICE_LABELS,
    admin_feedback_card_keyboard,
    admin_feedback_list_keyboard,
    admin_feedback_menu_keyboard,
    admin_poll_card_keyboard,
    admin_polls_keyboard,
    poll_none_label_keyboard,
    poll_preview_keyboard,
    poll_type_keyboard,
    user_poll_keyboard,
)
from app.services.feedback import (
    count_feedback_by_status,
    get_feedback,
    list_feedback,
    set_feedback_status,
)
from app.services.polls import (
    close_poll,
    create_poll,
    get_poll,
    get_poll_results,
    get_user_vote,
    list_polls,
    parse_poll_options,
    upsert_vote,
)
from app.services.users import get_active_users, get_user_by_telegram_id
from app.states import AdminPollStates
from app.utils import format_moscow_datetime, html_escape

router = Router()


def _is_admin(user_id: int) -> bool:
    return int(user_id) == int(settings.admin_id)


async def _deny(call_or_message) -> bool:
    if _is_admin(call_or_message.from_user.id):
        return False
    if isinstance(call_or_message, CallbackQuery):
        await call_or_message.answer("Нет доступа.", show_alert=True)
    else:
        await call_or_message.answer("Нет доступа.")
    return True


def _feedback_status_title(status: str) -> str:
    return {
        "new": "🆕 Новое",
        "in_work": "🟡 В работе",
        "done": "✅ Закрыто",
    }.get(status, status)


def _feedback_source_title(source: str) -> str:
    return {
        "idea": "Есть идея?",
        "question": "Не нашли ответ?",
    }.get(source, source)


def _format_feedback(item) -> str:
    username = f"@{html_escape(item['username'])}" if item["username"] else "—"
    body = html_escape(item["text"]) if item["text"] else "<i>Текст не указан — приложен файл.</i>"
    attachment = ""
    if item["file_type"]:
        file_name = f" ({html_escape(item['file_name'])})" if item["file_name"] else ""
        attachment = f"\n\n<b>Вложение:</b> {html_escape(item['file_type'])}{file_name}"
    return (
        f"💬 <b>Сообщение #{item['id']}</b>\n\n"
        f"Статус: {_feedback_status_title(item['status'])}\n"
        f"Источник: {html_escape(_feedback_source_title(item['source']))}\n"
        f"От: {html_escape(item['full_name'])}\n"
        f"Username: {username}\n"
        f"Telegram ID: <code>{item['user_id']}</code>\n"
        f"Роль: {html_escape(item['role'])}\n"
        f"Создано: {format_moscow_datetime(item['created_at'])}\n\n"
        f"{body}{attachment}"
    )


async def _send_feedback_attachment(bot: Bot, chat_id: int, item) -> None:
    file_id = item["file_id"]
    if not file_id:
        return
    caption = f"Вложение к сообщению #{item['id']}"
    try:
        if item["file_type"] == "photo":
            await bot.send_photo(chat_id, file_id, caption=caption)
        elif item["file_type"] == "video":
            await bot.send_video(chat_id, file_id, caption=caption)
        elif item["file_type"] == "audio":
            await bot.send_audio(chat_id, file_id, caption=caption)
        elif item["file_type"] == "voice":
            await bot.send_voice(chat_id, file_id, caption=caption)
        else:
            await bot.send_document(chat_id, file_id, caption=caption)
    except Exception:
        await bot.send_message(chat_id, "Вложение не удалось открыть повторно.")


@router.callback_query(F.data == "admin_feedback")
async def admin_feedback_menu_callback(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    counts = await count_feedback_by_status()
    await call.message.answer(
        "💬 <b>Обратная связь</b>\n\n"
        f"Новых сообщений: <b>{counts['new']}</b>\n"
        f"В работе: <b>{counts['in_work']}</b>\n"
        f"Закрыто: <b>{counts['done']}</b>\n\n"
        "Менеджеры пишут свободным текстом — без выбора между ошибкой, вопросом и предложением.",
        reply_markup=admin_feedback_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_feedback_list:"))
async def admin_feedback_list_callback(call: CallbackQuery):
    if await _deny(call):
        return
    list_filter = call.data.split(":", 1)[1]
    status = "new" if list_filter == "new" else None
    items = await list_feedback(status=status, limit=30)
    title = "🆕 Новые сообщения" if list_filter == "new" else "📋 Все сообщения"
    text = f"{title}\n\n"
    text += f"Найдено: {len(items)}" if items else "Сообщений пока нет."
    await call.message.answer(text, reply_markup=admin_feedback_list_keyboard(items, list_filter))
    await call.answer()


@router.callback_query(F.data.startswith("admin_feedback_open:"))
async def admin_feedback_open_callback(call: CallbackQuery, bot: Bot):
    if await _deny(call):
        return
    parts = call.data.split(":")
    feedback_id = int(parts[1])
    back_filter = parts[2] if len(parts) > 2 else "all"
    item = await get_feedback(feedback_id)
    if not item:
        await call.answer("Сообщение не найдено.", show_alert=True)
        return
    await call.message.answer(
        _format_feedback(item),
        reply_markup=admin_feedback_card_keyboard(feedback_id, item["status"], back_filter),
    )
    await _send_feedback_attachment(bot, call.message.chat.id, item)
    await call.answer()


@router.callback_query(F.data.startswith("admin_feedback_status:"))
async def admin_feedback_status_callback(call: CallbackQuery):
    if await _deny(call):
        return
    parts = call.data.split(":")
    feedback_id = int(parts[1])
    status = parts[2]
    back_filter = parts[3] if len(parts) > 3 else "all"
    if not await set_feedback_status(feedback_id, status):
        await call.answer("Сообщение не найдено.", show_alert=True)
        return
    item = await get_feedback(feedback_id)
    await call.message.answer(
        _format_feedback(item),
        reply_markup=admin_feedback_card_keyboard(feedback_id, item["status"], back_filter),
    )
    await call.answer("Статус обновлён")


@router.callback_query(F.data == "admin_poll_create")
async def admin_poll_create_callback(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    await call.message.answer(
        "🗳 <b>Новое голосование</b>\n\n"
        "Выберите формат. Менеджер голосует одним нажатием, а вариант отказа добавляется обязательно.",
        reply_markup=poll_type_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_poll_type:"))
async def admin_poll_type_callback(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    poll_type = call.data.split(":", 1)[1]
    if poll_type not in {"choice", "rating"}:
        await call.answer("Неизвестный формат.", show_alert=True)
        return
    await state.clear()
    await state.update_data(poll_type=poll_type)
    await state.set_state(AdminPollStates.waiting_question)
    await call.message.answer(
        "Напишите вопрос голосования одним сообщением.\n\n"
        "Он должен быть понятен без дополнительного объяснения.",
    )
    await call.answer()


@router.message(AdminPollStates.waiting_question)
async def admin_poll_question(message: Message, state: FSMContext):
    if await _deny(message):
        return
    question = (message.text or "").strip()
    if len(question) < 5:
        await message.answer("Вопрос слишком короткий. Напишите его понятнее.")
        return
    if len(question) > 1000:
        await message.answer("Вопрос слишком длинный. Сократите его до 1000 символов.")
        return
    data = await state.get_data()
    poll_type = data.get("poll_type")
    await state.update_data(question=question)
    if poll_type == "rating":
        await state.update_data(options=["1", "2", "3", "4", "5"], none_label="🚫 Не нужна")
        await state.set_state(AdminPollStates.waiting_publish)
        await message.answer(
            _poll_preview_text("rating", question, ["1", "2", "3", "4", "5"], "🚫 Не нужна"),
            reply_markup=poll_preview_keyboard(),
        )
        return
    await state.set_state(AdminPollStates.waiting_options)
    await message.answer(
        "Отправьте от 2 до 5 вариантов одним сообщением — каждый вариант с новой строки.\n\n"
        "Например:\nОставить все кнопки\nРедкие действия убрать в «Ещё»\nПоказывать кнопки по статусу"
    )


@router.message(AdminPollStates.waiting_options)
async def admin_poll_options(message: Message, state: FSMContext):
    if await _deny(message):
        return
    options = [line.strip(" •-\t") for line in (message.text or "").splitlines()]
    options = [item for item in options if item]
    if not 2 <= len(options) <= 5:
        await message.answer("Нужно от 2 до 5 непустых вариантов, каждый с новой строки.")
        return
    if any(len(item) > 300 for item in options):
        await message.answer("Один из вариантов длиннее 300 символов. Сократите формулировку.")
        return
    await state.update_data(options=options)
    await message.answer(
        "Как назвать обязательный вариант отказа?",
        reply_markup=poll_none_label_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_poll_none:"))
async def admin_poll_none_callback(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    choice = call.data.split(":", 1)[1]
    if choice == "custom":
        await state.set_state(AdminPollStates.waiting_none_custom)
        await call.message.answer("Напишите свой вариант отказа, например: «🚫 Сейчас ничего менять не нужно».")
        await call.answer()
        return
    labels = {
        "keep": "🚫 Оставить как есть",
        "none": "🚫 Ничего из этого не нужно",
    }
    none_label = labels.get(choice)
    if not none_label:
        await call.answer("Неизвестный вариант.", show_alert=True)
        return
    await state.update_data(none_label=none_label)
    await _show_poll_preview(call.message, state)
    await call.answer()


@router.message(AdminPollStates.waiting_none_custom)
async def admin_poll_none_custom(message: Message, state: FSMContext):
    if await _deny(message):
        return
    label = (message.text or "").strip()
    if not 3 <= len(label) <= 60:
        await message.answer("Надпись должна содержать от 3 до 60 символов.")
        return
    await state.update_data(none_label=label)
    await _show_poll_preview(message, state)


async def _show_poll_preview(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(AdminPollStates.waiting_publish)
    await message.answer(
        _poll_preview_text(
            data.get("poll_type"),
            data.get("question"),
            data.get("options", []),
            data.get("none_label", "🚫 Не нужно"),
        ),
        reply_markup=poll_preview_keyboard(),
    )


def _poll_preview_text(poll_type: str, question: str, options: list[str], none_label: str) -> str:
    lines = ["🗳 <b>Предпросмотр голосования</b>", "", f"<b>{html_escape(question)}</b>", ""]
    if poll_type == "rating":
        lines.append("Оценка: 1 — почти не нужна, 5 — очень нужна.")
    else:
        for index, option in enumerate(options):
            lines.append(f"<b>{CHOICE_LABELS[index]}</b> — {html_escape(option)}")
    lines.extend(["", html_escape(none_label)])
    return "\n".join(lines)


def _user_poll_text(poll) -> str:
    options = parse_poll_options(poll["options_json"])
    lines = ["🗳 <b>Небольшое голосование</b>", "", f"<b>{html_escape(poll['question'])}</b>", ""]
    if poll["poll_type"] == "rating":
        lines.append("1 — почти не нужна, 5 — очень нужна.")
    else:
        for index, option in enumerate(options):
            lines.append(f"<b>{CHOICE_LABELS[index]}</b> — {html_escape(option)}")
    lines.extend(["", "Выберите один вариант. Голос можно изменить повторным нажатием."])
    return "\n".join(lines)


@router.callback_query(AdminPollStates.waiting_publish, F.data == "admin_poll_publish")
async def admin_poll_publish_callback(call: CallbackQuery, state: FSMContext, bot: Bot):
    if await _deny(call):
        return
    data = await state.get_data()
    try:
        poll_id = await create_poll(
            poll_type=data.get("poll_type"),
            question=data.get("question"),
            options=data.get("options", []),
            none_label=data.get("none_label"),
            created_by=call.from_user.id,
        )
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    poll = await get_poll(poll_id)
    users = await get_active_users()
    delivered = 0
    failed = 0
    for user in users:
        telegram_id = int(user["telegram_id"])
        if telegram_id == int(settings.admin_id):
            continue
        if department_by_role(user["role"]) not in {"client", "purchasing"}:
            continue
        try:
            await bot.send_message(
                telegram_id,
                _user_poll_text(poll),
                reply_markup=user_poll_keyboard(poll),
            )
            delivered += 1
        except Exception:
            failed += 1

    await state.clear()
    await call.message.answer(
        f"✅ Голосование #{poll_id} опубликовано.\n\n"
        f"Доставлено: {delivered}\n"
        f"Не доставлено: {failed}",
        reply_markup=admin_feedback_menu_keyboard(),
    )
    await call.answer("Опубликовано")


@router.callback_query(F.data == "admin_poll_cancel")
async def admin_poll_cancel_callback(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    await call.message.answer("Создание голосования отменено.", reply_markup=admin_feedback_menu_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("poll_vote:"))
async def poll_vote_callback(call: CallbackQuery):
    parts = call.data.split(":")
    poll_id = int(parts[1])
    choice_key = parts[2]
    user = await get_user_by_telegram_id(call.from_user.id)
    if not user or int(user["is_active"] or 0) != 1:
        await call.answer("Нет доступа.", show_alert=True)
        return
    if not await upsert_vote(poll_id, call.from_user.id, choice_key):
        await call.answer("Голосование уже закрыто или вариант недоступен.", show_alert=True)
        return
    poll = await get_poll(poll_id)
    selected = await get_user_vote(poll_id, call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=user_poll_keyboard(poll, selected=selected))
    except Exception:
        pass
    await call.answer("Голос учтён")


@router.callback_query(F.data == "admin_polls")
async def admin_polls_callback(call: CallbackQuery):
    if await _deny(call):
        return
    polls = await list_polls(limit=30)
    await call.message.answer(
        f"📊 <b>Голосования</b>\n\nВсего показано: {len(polls)}" if polls else "📊 <b>Голосования</b>\n\nГолосований пока нет.",
        reply_markup=admin_polls_keyboard(polls),
    )
    await call.answer()


def _poll_results_text(result: dict) -> str:
    poll = result["poll"]
    options = parse_poll_options(poll["options_json"])
    total = int(result["total"])
    counts = result["counts"]
    lines = [
        f"🗳 <b>Голосование #{poll['id']}</b>",
        "",
        f"<b>{html_escape(poll['question'])}</b>",
        f"Статус: {'🟢 Активно' if poll['status'] == 'active' else '⚫ Завершено'}",
        f"Ответов: <b>{total}</b>",
        "",
    ]
    for index, option in enumerate(options):
        key = str(index)
        count = int(counts.get(key, 0))
        percent = round(count * 100 / total) if total else 0
        label = str(index + 1) if poll["poll_type"] == "rating" else CHOICE_LABELS[index]
        lines.append(f"<b>{label}</b> — {html_escape(option)}: {count} ({percent}%)")
    none_count = int(counts.get("none", 0))
    none_percent = round(none_count * 100 / total) if total else 0
    lines.append(f"{html_escape(poll['none_label'])}: {none_count} ({none_percent}%)")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("admin_poll_open:"))
async def admin_poll_open_callback(call: CallbackQuery):
    if await _deny(call):
        return
    poll_id = int(call.data.split(":", 1)[1])
    result = await get_poll_results(poll_id)
    if not result["poll"]:
        await call.answer("Голосование не найдено.", show_alert=True)
        return
    await call.message.answer(
        _poll_results_text(result),
        reply_markup=admin_poll_card_keyboard(poll_id, result["poll"]["status"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_poll_close:"))
async def admin_poll_close_callback(call: CallbackQuery):
    if await _deny(call):
        return
    poll_id = int(call.data.split(":", 1)[1])
    await close_poll(poll_id)
    result = await get_poll_results(poll_id)
    if not result["poll"]:
        await call.answer("Голосование не найдено.", show_alert=True)
        return
    await call.message.answer(
        _poll_results_text(result),
        reply_markup=admin_poll_card_keyboard(poll_id, result["poll"]["status"]),
    )
    await call.answer("Голосование завершено")
