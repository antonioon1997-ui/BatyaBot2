from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.domain import DEPARTMENT_CLIENT
from app.keyboards.common import ticket_work_menu_keyboard
from app.keyboards.tickets import ticket_filters_keyboard
from app.services.tickets import (
    get_filtered_tickets,
    get_incoming_tickets,
    get_outgoing_tickets,
    get_work_tickets,
)
from app.services.ui_context import get_ui_context
from app.services.ui_messages import send_ui_text
from app.services.ui_versions import pc_ticket_workspace_enabled

from .utils import department_by_role, get_current_user_and_admin, is_observer_role, row_get
from .views import (
    send_archive_menu,
    send_archive_page,
    send_overdue_page,
    send_tickets_list,
    show_main_menu,
)
from .workspace import show_ticket_workspace

router = Router()

WORKSPACE_LIST_TYPES = {"incoming", "work", "outgoing", "archive"}


async def _last_workspace_list_type(user_id: int) -> str:
    context = await get_ui_context(user_id)
    return context.list_type if context.list_type in WORKSPACE_LIST_TYPES else "incoming"


async def _deny_workspace_observer(target, user, admin_flag: bool) -> bool:
    if not is_observer_role(row_get(user, "role")) or admin_flag:
        return False
    if isinstance(target, CallbackQuery):
        await target.answer("Для наблюдателя доступны отдельные списки.", show_alert=True)
    else:
        await target.answer("Для наблюдателя доступны отдельные списки в главном меню.")
    return True


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
    if await _deny_workspace_observer(message, user, admin_flag):
        return

    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(
            message,
            list_type=await _last_workspace_list_type(message.from_user.id),
            page=None,
        )
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
    if await _deny_workspace_observer(call, user, admin_flag):
        return

    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(
            call,
            list_type=await _last_workspace_list_type(call.from_user.id),
            page=None,
        )
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(message, list_type="outgoing", page=0, filters={})
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(call, list_type="outgoing", page=0, filters={})
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(message, list_type="incoming", page=0, filters={})
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(call, list_type="incoming", page=0, filters={})
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(message, list_type="work", page=0, filters={})
        return
    tickets = await get_work_tickets() if admin_flag else await get_work_tickets(
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(call, list_type="work", page=0, filters={})
        return
    tickets = await get_work_tickets() if admin_flag else await get_work_tickets(
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(message, list_type="archive", page=0, filters={})
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
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(call, list_type="archive", page=0, filters={})
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
    list_type = call.data.split(":", 1)[1]
    if pc_ticket_workspace_enabled() and list_type in WORKSPACE_LIST_TYPES:
        await send_ui_text(
            call.bot,
            chat_id=call.from_user.id,
            text="🔽 <b>Фильтр списка</b>\n\nВыбери один фильтр. Он применяется только к текущему разделу.",
            reply_markup=ticket_filters_keyboard(list_type),
        )
        await call.answer()
        return
    await call.message.answer(
        "Выбери фильтр. Он применяется только к текущему списку.",
        reply_markup=ticket_filters_keyboard(list_type),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ticket_filter:"))
async def ticket_filter_callback(call: CallbackQuery):
    _, list_type, field, value = call.data.split(":", 3)
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return

    kwargs = {}
    if field == "status":
        kwargs["status"] = value
    elif field == "priority":
        kwargs["priority"] = value
    elif field == "category":
        kwargs["category"] = value
    elif field == "attachments":
        kwargs["has_attachments"] = value == "yes"
    elif field == "date":
        kwargs["date_days"] = int(value)
    elif field == "overdue":
        kwargs["overdue_only"] = True
    elif field == "department":
        kwargs["filter_department"] = value
    elif field == "clear":
        kwargs = {}

    if pc_ticket_workspace_enabled() and list_type in WORKSPACE_LIST_TYPES:
        await show_ticket_workspace(call, list_type=list_type, page=0, filters=kwargs)
        return

    department = None if admin_flag else department_by_role(row_get(user, "role"))
    tickets = await get_filtered_tickets(call.from_user.id, department, list_type, **kwargs)
    titles = {
        "outgoing": "📤 Исходящие",
        "incoming": "📥 Входящие",
        "work": "🛠 В работе",
        "archive": "📦 Архив",
    }
    await send_tickets_list(call, titles.get(list_type, "Тикеты") + " — результат фильтра", tickets)
