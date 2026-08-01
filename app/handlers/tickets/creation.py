import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import DEPARTMENT_CLIENT, DEPARTMENT_PURCHASING
from app.keyboards.productivity import duplicate_warning_keyboard
from app.keyboards.tickets import (
    cancel_create_keyboard,
    post_create_options_keyboard,
    ticket_category_keyboard,
)
from app.services.attachments import create_attachment
from app.services.ticket_messages import replace_ticket_message_bundle
from app.services.ui_messages import clear_ui_message_bundle, delete_trigger_message, send_ui_text
from app.services.tickets import (
    create_ticket,
    get_ticket_by_id,
    set_ticket_category,
    set_ticket_priority,
)
from app.services.work_management import find_open_duplicates
from app.states import CreateTicketStates
from app.utils import html_escape

from .utils import (
    department_by_role,
    extract_order_number,
    get_current_user_and_admin,
    get_department_name,
    is_observer_role,
    notify_department_about_ticket,
    optional_line,
    row_get,
)

router = Router()
logger = logging.getLogger(__name__)


async def start_create_ticket(message_or_call, state: FSMContext):
    telegram_id = message_or_call.from_user.id
    user, admin_flag = await get_current_user_and_admin(telegram_id)

    if not user:
        text = "У тебя пока нет доступа к боту. Отправь /start и дождись одобрения."

        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(text, show_alert=True)
        else:
            await message_or_call.answer(text)

        return

    if is_observer_role(row_get(user, "role")):
        text = "Наблюдатель может просматривать тикеты, но не создавать новые."

        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(text, show_alert=True)
        else:
            await message_or_call.answer(text)

        return

    await state.clear()
    await state.set_state(CreateTicketStates.waiting_text)

    text = (
        "Создание тикета.\n\n"
        "Отправь текст тикета одним сообщением.\n\n"
        "Если нужно вложение — отправь фото, документ или видео с подписью. "
        "Текст подписи станет описанием тикета.\n\n"
        "Номер заказа определяется автоматически.\n"
        "<b>По возможности пишите его в формате \"Заказ N\".</b>"
    )

    await send_ui_text(
        message_or_call.bot,
        chat_id=telegram_id,
        text=text,
        reply_markup=cancel_create_keyboard(),
    )
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

def parse_ticket_text(text: str):
    description = text.strip()
    order_number = extract_order_number(description)

    title = description.replace("\n", " ").strip()
    title = re.sub(r"\s+", " ", title)

    if not title:
        title = "Без темы"

    return title[:250], description, order_number

def extract_message_attachment(message: Message):
    file_id = None
    file_type = None
    file_name = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        file_name = message.document.file_name
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
        file_name = message.video.file_name

    if not file_id:
        return None

    return {
        "file_id": file_id,
        "file_type": file_type,
        "file_name": file_name,
    }

async def _persist_pending_ticket(target, state: FSMContext, pending: dict):
    ticket_id = await create_ticket(
        title=pending["title"],
        description=pending["description"],
        order_number=pending.get("order_number"),
        created_by=int(pending["created_by"]),
        executor_department=pending["executor_department"],
        requester_department=pending["requester_department"],
    )

    attachment = pending.get("attachment")
    if attachment:
        await create_attachment(
            ticket_id=ticket_id,
            file_id=attachment["file_id"],
            file_type=attachment["file_type"],
            file_name=attachment.get("file_name"),
            uploaded_by=int(pending["created_by"]),
        )

    await state.clear()
    message = target.message if isinstance(target, CallbackQuery) else target
    confirmation = await message.answer(
        f"✅ Тикет #{ticket_id} создан и отправлен в: {get_department_name(pending['executor_department'])}\n\n"
        "Дополнительные параметры необязательны. Можно ничего не выбирать.",
        reply_markup=post_create_options_keyboard(ticket_id),
    )
    try:
        await clear_ui_message_bundle(
            target.bot,
            chat_id=int(pending["created_by"]),
        )
    except Exception:
        logger.exception("Не удалось убрать экран создания тикета у пользователя %s", pending["created_by"])
    try:
        await replace_ticket_message_bundle(
            target.bot,
            chat_id=int(pending["created_by"]),
            ticket_id=ticket_id,
            new_message_ids=[int(confirmation.message_id)],
        )
    except Exception:
        # Подтверждение уже доставлено. Ошибка реестра не должна отменять создание тикета.
        logger.exception("Не удалось зарегистрировать карточку созданного тикета %s", ticket_id)

    order_line = optional_line("🔢 Заказ: ", pending.get("order_number"))
    attachments_line = "📎 Есть вложения: 1 шт.\n" if attachment else ""
    await notify_department_about_ticket(
        bot=target.bot,
        department=pending["executor_department"],
        text=(
            f"🆕 Новый входящий тикет #{ticket_id}\n\n"
            f"{order_line}"
            f"{attachments_line}"
            f"👤 Автор: {html_escape(pending.get('author_name') or pending['created_by'])}\n"
            f"📝 Описание:\n{html_escape(pending['description'])}"
        ),
        exclude_telegram_id=int(pending["created_by"]),
        ticket_id=ticket_id,
        use_ticket_actions=True,
    )
    return ticket_id


async def create_ticket_from_message(message: Message, state: FSMContext):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)

    if not user:
        await message.answer("Нет доступа.")
        return

    if is_observer_role(row_get(user, "role")):
        await message.answer("Наблюдатель может просматривать тикеты, но не создавать новые.")
        await state.clear()
        return

    user_department = department_by_role(row_get(user, "role"))

    if user_department == DEPARTMENT_CLIENT:
        executor_department = DEPARTMENT_PURCHASING
    elif user_department == DEPARTMENT_PURCHASING:
        executor_department = DEPARTMENT_CLIENT
    else:
        await message.answer("Не удалось определить отдел пользователя.")
        return

    text = message.text or message.caption
    if not text or not text.strip():
        await send_ui_text(
            message.bot,
            chat_id=message.from_user.id,
            text="Пришли текст тикета. Если отправляешь вложение, добавь к нему подпись.",
            reply_markup=cancel_create_keyboard(),
        )
        return

    title, description, order_number = parse_ticket_text(text)
    pending = {
        "title": title,
        "description": description,
        "order_number": order_number,
        "created_by": message.from_user.id,
        "requester_department": user_department,
        "executor_department": executor_department,
        "attachment": extract_message_attachment(message),
        "author_name": row_get(user, "full_name") or row_get(user, "username") or message.from_user.id,
    }

    duplicates = await find_open_duplicates(order_number) if order_number else []
    if duplicates:
        await state.set_state(CreateTicketStates.waiting_duplicate_confirmation)
        await state.update_data(pending_ticket=pending)
        lines = [
            f"⚠️ По заказу <b>{html_escape(order_number)}</b> уже есть открытые тикеты:",
            "",
        ]
        for ticket in duplicates[:5]:
            lines.append(f"#{ticket['id']} — {html_escape(str(ticket['description'])[:180])}")
        lines.append("\nПроверь существующий тикет или создай новый, если вопрос другой.")
        await send_ui_text(
            message.bot,
            chat_id=message.from_user.id,
            text="\n".join(lines),
            reply_markup=duplicate_warning_keyboard(duplicates),
        )
        return

    await _persist_pending_ticket(message, state, pending)


@router.callback_query(F.data == "duplicate_create_confirm")
async def callback_duplicate_create_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pending = data.get("pending_ticket")
    if not pending:
        await call.answer("Данные создания уже устарели. Начни создание заново.", show_alert=True)
        return
    if int(pending.get("created_by", 0)) != call.from_user.id:
        await call.answer("Это создание принадлежит другому пользователю.", show_alert=True)
        return
    ticket_id = await _persist_pending_ticket(call, state, pending)
    await call.answer(f"Тикет #{ticket_id} создан.")

@router.message(F.text == "➕ Создать тикет")
async def bottom_create_ticket(message: Message, state: FSMContext):
    await start_create_ticket(message, state)
    await delete_trigger_message(message)

@router.callback_query(F.data == "create_ticket")
async def callback_create_ticket(call: CallbackQuery, state: FSMContext):
    await start_create_ticket(call, state)

@router.message(CreateTicketStates.waiting_text)
async def process_ticket_text(message: Message, state: FSMContext):
    await create_ticket_from_message(message, state)

@router.callback_query(F.data == "confirm_create_ticket")
async def callback_confirm_create_ticket(call: CallbackQuery, state: FSMContext):
    await call.answer("Подтверждение больше не требуется. Отправь текст тикета одним сообщением.", show_alert=True)

@router.callback_query(F.data == "correct_order_number")
async def correct_order_number(call: CallbackQuery, state: FSMContext):
    await call.answer("Номер заказа теперь определяется автоматически из текста.", show_alert=True)

@router.callback_query(F.data == "add_attachments_before_create")
async def add_attachments_before_create(call: CallbackQuery, state: FSMContext):
    await call.answer("Отправь вложение сразу с подписью при создании тикета.", show_alert=True)

@router.callback_query(F.data == "cancel_create_ticket")
async def cancel_create_ticket_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await clear_ui_message_bundle(call.bot, chat_id=call.from_user.id)
    await call.answer("Создание тикета отменено.")

@router.callback_query(F.data == "noop")
async def noop_callback(call: CallbackQuery):
    await call.answer()



async def safe_edit_reply_markup(message, reply_markup) -> bool:
    """Обновляет inline-клавиатуру и игнорирует безвредную ошибку Telegram, если она не изменилась."""
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return False
        raise

@router.callback_query(F.data.startswith("ticket_priority:"))
async def ticket_priority_callback(call: CallbackQuery):
    _, ticket_id_raw, priority = call.data.split(":", 2)
    ticket_id = int(ticket_id_raw)
    ticket = await get_ticket_by_id(ticket_id)
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    if not ticket or (int(row_get(ticket, "created_by", 0)) != call.from_user.id and not admin_flag):
        await call.answer("Изменить приоритет может автор или администратор.", show_alert=True)
        return
    old_priority = row_get(ticket, "priority", "normal") or "normal"
    await set_ticket_priority(ticket_id, priority, call.from_user.id)
    updated = await get_ticket_by_id(ticket_id)
    await safe_edit_reply_markup(call.message, post_create_options_keyboard(ticket_id, row_get(updated, "priority"), row_get(updated, "category")))

    priority_rank = {"normal": 0, "important": 1, "urgent": 2}
    priority_names = {"normal": "🟢 Обычный", "important": "🟡 Важный", "urgent": "🔴 Срочный"}
    if priority_rank.get(priority, 0) > priority_rank.get(old_priority, 0):
        await notify_department_about_ticket(
            bot=call.bot,
            department=row_get(updated, "executor_department"),
            text=f"🚦 Тикет #{ticket_id}: срочность повышена до «{priority_names.get(priority, priority)}».",
            exclude_telegram_id=call.from_user.id,
            ticket_id=ticket_id,
            use_ticket_actions=False,
        )

    await call.answer("Приоритет сохранён.")

@router.callback_query(F.data.startswith("ticket_category_menu:"))
async def ticket_category_menu_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    ticket = await get_ticket_by_id(ticket_id)
    await safe_edit_reply_markup(call.message, ticket_category_keyboard(ticket_id, row_get(ticket, "category")))
    await call.answer()

@router.callback_query(F.data.startswith("ticket_category:"))
async def ticket_category_callback(call: CallbackQuery):
    _, ticket_id_raw, category = call.data.split(":", 2)
    ticket_id = int(ticket_id_raw)
    category_value = None if category == "none" else category
    ticket = await get_ticket_by_id(ticket_id)
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    if not ticket or (int(row_get(ticket, "created_by", 0)) != call.from_user.id and not admin_flag):
        await call.answer("Изменить тип может автор или администратор.", show_alert=True)
        return
    await set_ticket_category(ticket_id, category_value, call.from_user.id)
    updated = await get_ticket_by_id(ticket_id)
    await safe_edit_reply_markup(call.message, post_create_options_keyboard(ticket_id, row_get(updated, "priority"), row_get(updated, "category")))
    await call.answer("Тип тикета сохранён.")

@router.callback_query(F.data.startswith("ticket_options:"))
async def ticket_options_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    ticket = await get_ticket_by_id(ticket_id)
    await safe_edit_reply_markup(call.message, post_create_options_keyboard(ticket_id, row_get(ticket, "priority"), row_get(ticket, "category")))
    await call.answer()
