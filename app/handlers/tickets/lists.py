from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.domain import DEPARTMENT_CLIENT
from app.keyboards.common import ticket_work_menu_keyboard
from app.keyboards.tickets import ticket_filters_keyboard
from app.services.ui_messages import send_ui_text
from app.services.tickets import (
    get_filtered_tickets,
    get_incoming_tickets,
    get_outgoing_tickets,
    get_work_tickets,
)

from .utils import department_by_role, get_current_user_and_admin, is_observer_role, row_get
from .views import (
    send_archive_menu,
    send_archive_page,
    send_overdue_page,
    send_tickets_list,
    show_main_menu,
)

router = Router()


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)
    await show_main_menu(message, user, admin_flag)

@router.message(F.text == "📂 Работа с тикетами")
async def bottom_ticket_work_menu(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)
    if not user:
        await message.answer("Нет доступа.")
        return
    if is_observer_role(row_get(user, "role")) and not admin_flag:
        await message.answer("Для наблюдателя доступны отдельные списки в главном меню.")
        return
    await send_ui_text(
        message.bot,
        chat_id=message.from_user.id,
        text="Выберите раздел работы с тикетами:",
        reply_markup=ticket_work_menu_keyboard(),
    )


@router.callback_query(F.data == "ticket_work_menu")
async def callback_ticket_work_menu(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    if is_observer_role(row_get(user, "role")) and not admin_flag:
        await call.answer("Для наблюдателя доступны отдельные списки.", show_alert=True)
        return
    await send_ui_text(
        call.bot,
        chat_id=call.from_user.id,
        text="Выберите раздел работы с тикетами:",
        reply_markup=ticket_work_menu_keyboard(),
    )
    await call.answer()


@router.message(Command("overdue"))
async def cmd_overdue(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)

    if not user:
        await message.answer("Нет доступа.")
        return

    department = department_by_role(row_get(user, "role"))

    if department != DEPARTMENT_CLIENT and not admin_flag:
        await message.answer("Этот раздел доступен клиентскому отделу и администратору.")
        return

    await send_overdue_page(message, page=0)

@router.callback_query(F.data.startswith("overdue_page:"))
async def callback_overdue_page(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return

    department = department_by_role(row_get(user, "role"))

    if department != DEPARTMENT_CLIENT and not admin_flag:
        await call.answer("Нет доступа.", show_alert=True)
        return

    try:
        page = int(call.data.split(":")[1])
    except Exception:
        page = 0

    await send_overdue_page(call, page=page)

@router.message(F.text == "📤 Исходящие")
async def bottom_outgoing_tickets(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)

    if not user:
        await message.answer("Нет доступа.")
        return

    if is_observer_role(row_get(user, "role")):
        await message.answer("Этот раздел недоступен наблюдателю.")
        return

    tickets = await get_outgoing_tickets(message.from_user.id)
    await send_tickets_list(message, "📤 Исходящие тикеты", tickets)

@router.callback_query(F.data == "outgoing_tickets")
async def callback_outgoing_tickets(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return

    if is_observer_role(row_get(user, "role")):
        await call.answer("Этот раздел недоступен наблюдателю.", show_alert=True)
        return

    tickets = await get_outgoing_tickets(call.from_user.id)
    await send_tickets_list(call, "📤 Исходящие тикеты", tickets)

@router.message(F.text == "📥 Входящие")
async def bottom_incoming_tickets(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)

    if not user:
        await message.answer("Нет доступа.")
        return

    if is_observer_role(row_get(user, "role")):
        await message.answer("Этот раздел недоступен наблюдателю.")
        return

    department = department_by_role(row_get(user, "role"))

    if not department and not admin_flag:
        await message.answer("Не удалось определить твой отдел.")
        return

    tickets = await get_incoming_tickets(department=department if department else None)
    await send_tickets_list(message, "📥 Входящие тикеты", tickets)

@router.callback_query(F.data == "incoming_tickets")
async def callback_incoming_tickets(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return

    if is_observer_role(row_get(user, "role")):
        await call.answer("Этот раздел недоступен наблюдателю.", show_alert=True)
        return

    department = department_by_role(row_get(user, "role"))

    if not department and not admin_flag:
        await call.answer("Не удалось определить твой отдел.", show_alert=True)
        return

    tickets = await get_incoming_tickets(department=department if department else None)
    await send_tickets_list(call, "📥 Входящие тикеты", tickets)

@router.message(F.text == "🛠 В работе")
async def bottom_work_tickets(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)

    if not user:
        await message.answer("Нет доступа.")
        return

    if is_observer_role(row_get(user, "role")):
        await message.answer("Этот раздел недоступен наблюдателю.")
        return

    department = department_by_role(row_get(user, "role"))

    if admin_flag:
        tickets = await get_work_tickets()
    else:
        tickets = await get_work_tickets(
            telegram_id=message.from_user.id,
            department=department,
        )

    await send_tickets_list(message, "🛠 Тикеты в работе", tickets)

@router.callback_query(F.data == "work_tickets")
async def callback_work_tickets(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return

    if is_observer_role(row_get(user, "role")):
        await call.answer("Этот раздел недоступен наблюдателю.", show_alert=True)
        return

    department = department_by_role(row_get(user, "role"))

    if admin_flag:
        tickets = await get_work_tickets()
    else:
        tickets = await get_work_tickets(
            telegram_id=call.from_user.id,
            department=department,
        )

    await send_tickets_list(call, "🛠 Тикеты в работе", tickets)

@router.message(F.text == "📦 Архив")
async def bottom_archive_tickets(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)

    if not user:
        await message.answer("Нет доступа.")
        return

    if is_observer_role(row_get(user, "role")):
        await message.answer("Используй раздел “✅ Закрытые тикеты”.")
        return

    await send_archive_menu(message)

@router.callback_query(F.data == "archive_tickets")
async def callback_archive_tickets(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return

    if is_observer_role(row_get(user, "role")):
        await call.answer("Используй раздел “Закрытые тикеты”.", show_alert=True)
        return

    await send_archive_menu(call)

@router.callback_query(F.data.startswith("archive_page:"))
async def callback_archive_page(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return

    if is_observer_role(row_get(user, "role")):
        await call.answer("Нет доступа к этому архиву.", show_alert=True)
        return

    parts = call.data.split(":")

    if len(parts) != 3:
        await call.answer("Некорректная страница архива.", show_alert=True)
        return

    archive_type = parts[1]

    try:
        page = int(parts[2])
    except Exception:
        page = 0

    await send_archive_page(call, archive_type=archive_type, page=page)

@router.callback_query(F.data.startswith("ticket_filters:"))
async def ticket_filters_menu_callback(call: CallbackQuery):
    list_type = call.data.split(":")[1]
    await call.message.answer("Выбери фильтр. Он применяется только к текущему списку.", reply_markup=ticket_filters_keyboard(list_type))
    await call.answer()

@router.callback_query(F.data.startswith("ticket_filter:"))
async def ticket_filter_callback(call: CallbackQuery):
    _, list_type, field, value = call.data.split(":", 3)
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    department = None if admin_flag else department_by_role(row_get(user, "role"))
    kwargs = {}
    if field == "status": kwargs["status"] = value
    elif field == "priority": kwargs["priority"] = value
    elif field == "category": kwargs["category"] = value
    elif field == "attachments": kwargs["has_attachments"] = value == "yes"
    elif field == "date": kwargs["date_days"] = int(value)
    elif field == "overdue": kwargs["overdue_only"] = True
    elif field == "department": kwargs["filter_department"] = value
    tickets = await get_filtered_tickets(call.from_user.id, department, list_type, **kwargs)
    titles = {"outgoing": "📤 Исходящие", "incoming": "📥 Входящие", "work": "🛠 В работе", "archive": "📦 Архив"}
    await send_tickets_list(call, titles.get(list_type, "Тикеты") + " — результат фильтра", tickets)
