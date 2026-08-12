import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.states import AdminStates
from app.keyboards.admin import (
    admin_menu,
    admin_users_section_menu,
    admin_tickets_section_menu,
    admin_stats_section_menu,
    admin_system_section_menu,
    admin_access_requests_menu,
    admin_users_menu,
    admin_tickets_menu,
    admin_access_requests_list_keyboard,
    admin_request_card_keyboard,
    users_list_keyboard,
    user_admin_keyboard,
    change_role_keyboard,
    admin_tickets_list_keyboard,
    admin_tickets_paginated_keyboard,
    admin_ticket_action_keyboard,
    admin_ticket_status_keyboard,
    admin_ticket_reminder_departments_keyboard,
    admin_ticket_reminder_categories_keyboard,
    manual_ticket_reminder_open_keyboard,
    admin_notes_keyboard,
    admin_note_card_keyboard,
)
from app.keyboards.common import main_menu_for_role
from app.services.main_menu_dashboard import build_main_menu_text
from app.services.admin_notes import list_notes, get_note, create_note, update_note, delete_note
from app.services.ui_messages import send_ui_text
from app.services.ui_versions import pc_ticket_workspace_enabled
from app.services.users import (
    get_user_by_telegram_id,
    get_access_requests,
    get_access_request_by_id,
    approve_user,
    reject_user,
    get_all_users,
    get_users_by_status,
    deactivate_user,
    restore_user,
    set_user_role,
    get_user_tickets_summary,
)
from app.services.tickets import (
    get_ticket_by_id_admin,
    get_tickets_by_user_admin,
    get_admin_tickets_page,
    count_admin_tickets,
    soft_delete_ticket,
    restore_ticket,
    set_ticket_status_admin,
    get_admin_ticket_stats,
    get_setting,
    set_setting,
    count_tickets_for_department_reminder,
    get_active_users_by_department,
)
from app.utils import format_moscow_datetime, html_escape

router = Router()
logger = logging.getLogger(__name__)

ADMIN_TICKETS_PAGE_SIZE = 10


def is_admin_user(telegram_id: int) -> bool:
    return int(telegram_id) == int(settings.admin_id)


async def deny_if_not_admin(message_or_callback) -> bool:
    telegram_id = message_or_callback.from_user.id

    if is_admin_user(telegram_id):
        return False

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer("Нет доступа.", show_alert=True)
    else:
        await message_or_callback.answer("Нет доступа.")

    return True


def role_title(role: str | None) -> str:
    titles = {
        "purchaser": "Отдел закупки",
        "client": "Клиентский отдел",
        "observer": "Наблюдатель",
    }

    return titles.get(role, html_escape(role) if role else "Не указана")


def request_status_title(status: str | None) -> str:
    titles = {
        "new": "Новая",
        "approved": "Одобрена",
        "rejected": "Отклонена",
    }

    return titles.get(status, html_escape(status) if status else "Не указан")


def ticket_status_title(status: str | None) -> str:
    titles = {
        "new": "Новый",
        "in_work": "В работе",
        "waiting_confirmation": "Ожидает подтверждения",
        "done": "Закрыт",
        "cancelled": "Отменён",
    }

    return titles.get(status, html_escape(status) if status else "Не указан")


def department_title(department: str | None) -> str:
    titles = {
        "purchasing": "Закупка",
        "client": "Клиентский отдел",
    }

    return titles.get(department, html_escape(department) if department else "Не указан")


def has_text_value(value) -> bool:
    if value is None:
        return False

    return bool(str(value).strip())


def optional_line(prefix: str, value) -> str:
    if not has_text_value(value):
        return ""

    return f"{prefix}{html_escape(str(value).strip())}\n"


def format_ticket_short(ticket) -> str:
    deleted_icon = "🗑 " if ticket["is_deleted"] == 1 else ""
    status = ticket_status_title(ticket["status"])
    title = html_escape(ticket["title"] or "Без названия")
    order_line = optional_line("Заказ: ", ticket["order_number"])

    return (
        f"{deleted_icon}<b>#{ticket['id']}</b> — {title}\n"
        f"Статус: {status}\n"
        f"{order_line}"
        f"Создан: {format_moscow_datetime(ticket['created_at'])}\n"
    )


def format_ticket_detail(ticket) -> str:
    order_line = optional_line("<b>Номер заказа:</b> ", ticket["order_number"])

    return (
        f"<b>Тикет #{ticket['id']}</b>\n\n"
        f"<b>Название:</b> {html_escape(ticket['title'])}\n"
        f"<b>Описание:</b> {html_escape(ticket['description'])}\n"
        f"{order_line}"
        f"<b>Тип:</b> {html_escape(ticket['ticket_type'])}\n"
        f"<b>Направление:</b> {html_escape(ticket['direction'])}\n"
        f"<b>Статус:</b> {ticket_status_title(ticket['status'])}\n"
        f"<b>Решение:</b> {html_escape(ticket['resolution'])}\n"
        f"<b>Админ-заметка:</b> {html_escape(ticket['admin_note'])}\n"
        f"<b>Создан:</b> {format_moscow_datetime(ticket['created_at'])}\n"
        f"<b>Обновлён:</b> {format_moscow_datetime(ticket['updated_at'])}\n"
        f"<b>Закрыт:</b> {format_moscow_datetime(ticket['closed_at'])}\n"
        f"<b>Переоткрыт:</b> {format_moscow_datetime(ticket['reopened_at'])}"
    )


def format_access_request_card(request) -> str:
    username = html_escape(request["username"])
    full_name = html_escape(request["full_name"])
    processed_by = html_escape(request["processed_by"])

    return (
        f"<b>Заявка #{request['id']}</b>\n\n"
        f"<b>Telegram ID:</b> <code>{request['telegram_id']}</code>\n"
        f"<b>Username:</b> @{username}\n"
        f"<b>Имя:</b> {full_name}\n"
        f"<b>Статус:</b> {request_status_title(request['status'])}\n"
        f"<b>Создана:</b> {format_moscow_datetime(request['created_at'])}\n"
        f"<b>Обработана:</b> {format_moscow_datetime(request['processed_at'])}\n"
        f"<b>Кем обработана:</b> {processed_by}"
    )


def format_user_card(user, summary=None) -> str:
    username = html_escape(user["username"])
    full_name = html_escape(user["full_name"])
    status = "✅ Активен" if user["is_active"] == 1 else "🚫 Отключён"

    text = (
        f"<b>Пользователь</b>\n\n"
        f"<b>Telegram ID:</b> <code>{user['telegram_id']}</code>\n"
        f"<b>Username:</b> @{username}\n"
        f"<b>Имя:</b> {full_name}\n"
        f"<b>Роль:</b> {role_title(user['role'])}\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Создан:</b> {format_moscow_datetime(user['created_at'])}\n"
        f"<b>Обновлён:</b> {format_moscow_datetime(user['updated_at'])}\n"
        f"<b>Отключён:</b> {format_moscow_datetime(user['deactivated_at'])}\n"
        f"<b>Кем отключён:</b> {html_escape(user['deactivated_by'])}\n"
        f"<b>Восстановлен:</b> {format_moscow_datetime(user['restored_at'])}\n"
        f"<b>Кем восстановлен:</b> {html_escape(user['restored_by'])}\n"
        f"<b>Выходные:</b> {html_escape(user['day_off_start']) if user['day_off_start'] else '—'}"
        f" — {html_escape(user['day_off_end']) if user['day_off_end'] else '—'}"
    )

    if summary:
        text += (
            f"\n\n<b>Тикеты пользователя:</b>\n"
            f"Всего: {summary['total'] or 0}\n"
            f"Видимых: {summary['visible_total'] or 0}\n"
            f"Удалённых: {summary['deleted_total'] or 0}\n"
            f"Открытых: {summary['open_total'] or 0}\n"
            f"Закрытых: {summary['closed_total'] or 0}"
        )

    return text


def format_admin_ticket_card(ticket) -> str:
    deleted_status = "🗑 Удалён" if ticket["is_deleted"] == 1 else "✅ Видимый"

    text = format_ticket_detail(ticket)

    text += (
        f"\n\n<b>Админская информация:</b>\n"
        f"<b>Видимость:</b> {deleted_status}\n"
        f"<b>Исключён из статистики:</b> {'Да' if ticket['excluded_from_stats'] == 1 else 'Нет'}\n"
        f"<b>Создал:</b> <code>{ticket['created_by']}</code>\n"
        f"<b>Исполнитель:</b> {department_title(ticket['executor_department'])}\n"
        f"<b>Отправитель:</b> {department_title(ticket['requester_department'])}\n"
        f"<b>Взят в работу:</b> {ticket['taken_by'] or '—'}\n"
        f"<b>Удалён:</b> {format_moscow_datetime(ticket['deleted_at'])}\n"
        f"<b>Кем удалён:</b> {ticket['deleted_by'] or '—'}\n"
        f"<b>Восстановлен:</b> {format_moscow_datetime(ticket['restored_at'])}\n"
        f"<b>Кем восстановлен:</b> {ticket['restored_by'] or '—'}"
    )

    return text


def is_valid_time(value: str) -> bool:
    if not value:
        return False

    value = value.strip()
    parts = value.split(":")

    if len(parts) != 2:
        return False

    hour_raw, minute_raw = parts

    if not hour_raw.isdigit() or not minute_raw.isdigit():
        return False

    hour = int(hour_raw)
    minute = int(minute_raw)

    if hour < 0 or hour > 23:
        return False

    if minute < 0 or minute > 59:
        return False

    return True


def normalize_admin_ticket_filter(value: str | None) -> str:
    if value in {"all", "open", "closed", "deleted"}:
        return value

    return "all"


def admin_tickets_title(ticket_filter: str) -> str:
    titles = {
        "all": "Все видимые тикеты",
        "open": "Открытые тикеты",
        "closed": "Закрытые тикеты",
        "deleted": "Удалённые тикеты",
    }

    return titles.get(ticket_filter, "Все видимые тикеты")


def build_admin_tickets_page_text(tickets, title: str, page: int, total: int, page_size: int = ADMIN_TICKETS_PAGE_SIZE) -> str:
    total_pages = (total + page_size - 1) // page_size if total else 1

    text = (
        f"<b>{html_escape(title)}</b>\n\n"
        f"Страница {page + 1} из {total_pages}. Всего тикетов: {total}\n\n"
    )

    for ticket in tickets:
        text += format_ticket_short(ticket) + "\n"

    return text


async def send_admin_tickets_page(message_or_callback, ticket_filter: str = "all", page: int = 0):
    ticket_filter = normalize_admin_ticket_filter(ticket_filter)

    if page < 0:
        page = 0

    total = await count_admin_tickets(ticket_filter)
    title = admin_tickets_title(ticket_filter)

    if not total:
        if pc_ticket_workspace_enabled():
            await send_ui_text(
                message_or_callback.bot,
                chat_id=message_or_callback.from_user.id,
                text=f"{title}: список пуст.",
                reply_markup=admin_tickets_menu(),
            )
        else:
            target_message = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
            await target_message.answer(
                f"{title}: список пуст.",
                reply_markup=admin_tickets_menu(),
            )

        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer()

        return

    total_pages = (total + ADMIN_TICKETS_PAGE_SIZE - 1) // ADMIN_TICKETS_PAGE_SIZE

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    offset = page * ADMIN_TICKETS_PAGE_SIZE

    tickets = await get_admin_tickets_page(
        ticket_filter=ticket_filter,
        limit=ADMIN_TICKETS_PAGE_SIZE,
        offset=offset,
    )

    text = build_admin_tickets_page_text(
        tickets=tickets,
        title=title,
        page=page,
        total=total,
        page_size=ADMIN_TICKETS_PAGE_SIZE,
    )

    keyboard = admin_tickets_paginated_keyboard(
        tickets=tickets,
        ticket_filter=ticket_filter,
        page=page,
        total=total,
        page_size=ADMIN_TICKETS_PAGE_SIZE,
    )

    if pc_ticket_workspace_enabled():
        await send_ui_text(
            message_or_callback.bot,
            chat_id=message_or_callback.from_user.id,
            text=text,
            reply_markup=keyboard,
        )
    else:
        target_message = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
        await target_message.answer(
            text,
            reply_markup=keyboard,
        )

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer()


async def send_admin_menu(message: Message):
    if await deny_if_not_admin(message):
        return

    await send_ui_text(
        message.bot,
        chat_id=message.from_user.id,
        text="⚙️ <b>Админка</b>\n\nВыбери раздел:",
        reply_markup=admin_menu(),
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    await send_admin_menu(message)


@router.message(Command("users"))
async def cmd_users(message: Message):
    if await deny_if_not_admin(message):
        return

    await message.answer(
        "📋 <b>Пользователи</b>\n\nВыбери список:",
        reply_markup=admin_users_menu()
    )


@router.message(Command("requests"))
async def cmd_requests(message: Message):
    if await deny_if_not_admin(message):
        return

    await message.answer(
        "👥 <b>Заявки на доступ</b>\n\nВыбери список:",
        reply_markup=admin_access_requests_menu()
    )


@router.message(Command("all_tickets"))
async def cmd_all_tickets(message: Message):
    if await deny_if_not_admin(message):
        return

    await send_admin_tickets_page(message, ticket_filter="all", page=0)


@router.message(Command("deleted_tickets"))
async def cmd_deleted_tickets(message: Message):
    if await deny_if_not_admin(message):
        return

    await send_admin_tickets_page(message, ticket_filter="deleted", page=0)


@router.message(Command("reminder_time"))
async def cmd_reminder_time(message: Message, state: FSMContext):
    if await deny_if_not_admin(message):
        return

    await state.set_state(AdminStates.waiting_reminder_time)

    current_time = await get_setting("reminder_time", settings.reminder_time)

    await message.answer(
        "⏰ <b>Настройка времени ежедневных напоминаний</b>\n\n"
        f"Сейчас установлено: <b>{current_time}</b>\n\n"
        "Пришли новое время в формате <code>HH:MM</code>, например <code>08:50</code>."
    )


@router.message(F.text == "⚙️ Админка")
async def bottom_admin_menu(message: Message):
    await send_admin_menu(message)


@router.callback_query(F.data == "admin_menu")
async def callback_admin_menu(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text="⚙️ <b>Админка</b>\n\nВыбери раздел:",
        reply_markup=admin_menu(),
    )

    await callback.answer()


@router.callback_query(F.data == "admin_section_users")
async def callback_admin_section_users(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text="👥 <b>Пользователи и доступ</b>",
        reply_markup=admin_users_section_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_section_tickets")
async def callback_admin_section_tickets(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text="🎫 <b>Тикеты и напоминания</b>",
        reply_markup=admin_tickets_section_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_section_stats")
async def callback_admin_section_stats(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text="📊 <b>Статистика и экспорт</b>",
        reply_markup=admin_stats_section_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_section_system")
async def callback_admin_section_system(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text="🔄 <b>Бот и обновления</b>",
        reply_markup=admin_system_section_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_ticket_reminders")
async def callback_admin_ticket_reminders(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    if pc_ticket_workspace_enabled():
        await send_ui_text(
            callback.bot,
            chat_id=callback.from_user.id,
            text="🔔 <b>Напомнить о тикетах</b>\n\nВыбери подразделение:",
            reply_markup=admin_ticket_reminder_departments_keyboard(),
        )
    else:
        await callback.message.answer(
            "🔔 <b>Напомнить о тикетах</b>\n\nВыбери подразделение:",
            reply_markup=admin_ticket_reminder_departments_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_reminder_department:"))
async def callback_admin_ticket_reminder_department(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    department = callback.data.split(":", 1)[1]
    if department not in {"purchasing", "client"}:
        await callback.answer("Неизвестное подразделение.", show_alert=True)
        return

    if pc_ticket_workspace_enabled():
        await send_ui_text(
            callback.bot,
            chat_id=callback.from_user.id,
            text=f"Подразделение: <b>{department_title(department)}</b>\n\nВыбери категорию тикетов:",
            reply_markup=admin_ticket_reminder_categories_keyboard(department),
        )
    else:
        await callback.message.answer(
            f"Подразделение: <b>{department_title(department)}</b>\n\nВыбери категорию тикетов:",
            reply_markup=admin_ticket_reminder_categories_keyboard(department),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_reminder_send:"))
async def callback_admin_ticket_reminder_send(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные параметры напоминания.", show_alert=True)
        return

    _, department, category = parts
    if department not in {"purchasing", "client"} or category not in {"new", "work"}:
        await callback.answer("Некорректные параметры напоминания.", show_alert=True)
        return

    category_title = "Не в работе" if category == "new" else "В работе"
    ticket_count = await count_tickets_for_department_reminder(department, category)

    if ticket_count <= 0:
        await callback.message.answer(
            f"Подходящих тикетов нет.\n\n"
            f"Отдел: <b>{department_title(department)}</b>\n"
            f"Категория: <b>{category_title}</b>",
            reply_markup=admin_menu(),
        )
        await callback.answer()
        return

    recipients = await get_active_users_by_department(department)
    delivered = 0
    errors = 0
    reminder_text = (
        "🔔 <b>Напоминание о тикетах</b>\n\n"
        "В вашем отделе есть тикеты, требующие внимания.\n\n"
        f"Категория: <b>{category_title}</b>\n"
        f"Количество: <b>{ticket_count}</b>\n\n"
        "Откройте бота, чтобы посмотреть список."
    )

    for recipient in recipients:
        try:
            await callback.bot.send_message(
                chat_id=int(recipient["telegram_id"]),
                text=reminder_text,
                reply_markup=manual_ticket_reminder_open_keyboard(),
            )
            delivered += 1
        except Exception:
            errors += 1
            logger.exception("Не удалось отправить ручное напоминание пользователю %s", recipient["telegram_id"])

    await callback.message.answer(
        "✅ <b>Рассылка завершена</b>\n\n"
        f"Отдел: {department_title(department)}\n"
        f"Категория: {category_title}\n"
        f"Найдено тикетов: {ticket_count}\n"
        f"Получателей: {len(recipients)}\n"
        f"Успешно доставлено: {delivered}\n"
        f"Ошибок доставки: {errors}",
        reply_markup=admin_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_reminder_time")
async def callback_admin_reminder_time(callback: CallbackQuery, state: FSMContext):
    if await deny_if_not_admin(callback):
        return

    await state.set_state(AdminStates.waiting_reminder_time)

    current_time = await get_setting("reminder_time", settings.reminder_time)

    await callback.message.answer(
        "⏰ <b>Настройка времени ежедневных напоминаний</b>\n\n"
        f"Сейчас установлено: <b>{current_time}</b>\n\n"
        "Пришли новое время в формате <code>HH:MM</code>, например <code>08:50</code>.\n\n"
        "После изменения перезапусти бота, чтобы планировщик применил новое время."
    )

    await callback.answer()


@router.message(AdminStates.waiting_reminder_time)
async def process_reminder_time(message: Message, state: FSMContext):
    if await deny_if_not_admin(message):
        return

    value = message.text.strip() if message.text else ""

    if not is_valid_time(value):
        await message.answer(
            "Некорректное время.\n\n"
            "Пришли время в формате <code>HH:MM</code>, например <code>08:50</code>."
        )
        return

    normalized_time = f"{int(value.split(':')[0]):02d}:{int(value.split(':')[1]):02d}"

    await set_setting("reminder_time", normalized_time)
    await state.clear()

    await message.answer(
        "✅ Время напоминаний обновлено.\n\n"
        f"Новое время: <b>{normalized_time}</b>\n\n"
        "Перезапусти бота, чтобы планировщик применил новое время.",
        reply_markup=admin_menu()
    )


@router.callback_query(F.data == "admin_access_requests")
async def callback_admin_access_requests(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text="👥 <b>Заявки на доступ</b>\n\nВыбери список:",
        reply_markup=admin_access_requests_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_access_requests:"))
async def callback_admin_access_requests_by_status(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    status = callback.data.split(":")[1]

    if status == "all":
        requests = await get_access_requests(status=None, limit=30)
        title = "Все заявки"
    else:
        requests = await get_access_requests(status=status, limit=30)
        title = {
            "new": "Новые заявки",
            "approved": "Одобренные заявки",
            "rejected": "Отклонённые заявки",
        }.get(status, "Заявки")

    if not requests:
        await callback.message.answer(
            f"{title}: список пуст.",
            reply_markup=admin_access_requests_menu()
        )
        await callback.answer()
        return

    text = f"<b>{title}:</b>\n\n"

    for request in requests:
        username = request["username"] or "—"
        full_name = request["full_name"] or "—"

        text += (
            f"<b>#{request['id']}</b> — {request_status_title(request['status'])}\n"
            f"ID: <code>{request['telegram_id']}</code>\n"
            f"Username: @{html_escape(username)}\n"
            f"Имя: {html_escape(full_name)}\n"
            f"Дата: {format_moscow_datetime(request['created_at'])}\n\n"
        )

    await callback.message.answer(
        text,
        reply_markup=admin_access_requests_list_keyboard(requests)
    )

    await callback.answer()


@router.callback_query(F.data.startswith("admin_request:"))
async def callback_admin_request_card(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    request_id = int(callback.data.split(":")[1])
    request = await get_access_request_by_id(request_id)

    if not request:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    await callback.message.answer(
        format_access_request_card(request),
        reply_markup=admin_request_card_keyboard(request)
    )

    await callback.answer()


@router.callback_query(F.data.startswith("approve_user:"))
async def callback_approve_user(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    _, role, telegram_id_raw = callback.data.split(":")
    telegram_id = int(telegram_id_raw)

    await approve_user(
        telegram_id=telegram_id,
        role=role,
        admin_telegram_id=callback.from_user.id
    )

    await callback.message.answer(
        f"✅ Пользователь <code>{telegram_id}</code> одобрен.\n"
        f"Роль: <b>{role_title(role)}</b>"
    )

    try:
        await callback.bot.send_message(
            telegram_id,
            f"✅ Твоя заявка на доступ одобрена.\n"
            f"Роль: <b>{role_title(role)}</b>"
        )
    except Exception:
        logger.exception("Не удалось отправить административное уведомление пользователю %s", telegram_id)

    await callback.answer()


@router.callback_query(F.data.startswith("reject_user:"))
async def callback_reject_user(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    telegram_id = int(callback.data.split(":")[1])

    await reject_user(
        telegram_id=telegram_id,
        admin_telegram_id=callback.from_user.id
    )

    await callback.message.answer(
        f"❌ Заявка пользователя <code>{telegram_id}</code> отклонена."
    )

    try:
        await callback.bot.send_message(
            telegram_id,
            "❌ Твоя заявка на доступ отклонена."
        )
    except Exception:
        logger.exception("Не удалось отправить административное уведомление пользователю %s", telegram_id)

    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def callback_admin_users(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text="📋 <b>Пользователи</b>\n\nВыбери список:",
        reply_markup=admin_users_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_users:"))
async def callback_admin_users_by_status(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    status = callback.data.split(":")[1]

    if status == "active":
        users = await get_users_by_status(1)
        title = "Активные пользователи"
    elif status == "inactive":
        users = await get_users_by_status(0)
        title = "Отключённые пользователи"
    else:
        users = await get_all_users()
        title = "Все пользователи"

    if not users:
        await callback.message.answer(
            f"{title}: список пуст.",
            reply_markup=admin_users_menu()
        )
        await callback.answer()
        return

    text = f"<b>{title}:</b>\n\n"

    for user in users:
        status_icon = "✅" if user["is_active"] == 1 else "🚫"
        username = user["username"] or "—"
        full_name = user["full_name"] or "—"

        text += (
            f"{status_icon} <b>{html_escape(full_name)}</b>\n"
            f"ID: <code>{user['telegram_id']}</code>\n"
            f"Username: @{html_escape(username)}\n"
            f"Роль: {role_title(user['role'])}\n\n"
        )

    await callback.message.answer(
        text,
        reply_markup=users_list_keyboard(users)
    )

    await callback.answer()


@router.callback_query(F.data.startswith("admin_user:"))
async def callback_admin_user_card(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    telegram_id = int(callback.data.split(":")[1])
    user = await get_user_by_telegram_id(telegram_id)

    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    summary = await get_user_tickets_summary(telegram_id)

    await callback.message.answer(
        format_user_card(user, summary),
        reply_markup=user_admin_keyboard(
            user_id=telegram_id,
            is_active=user["is_active"] == 1
        )
    )

    await callback.answer()


@router.callback_query(F.data.startswith("deactivate_user:"))
async def callback_deactivate_user(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    telegram_id = int(callback.data.split(":")[1])

    if telegram_id == callback.from_user.id:
        await callback.answer("Нельзя отключить самого себя.", show_alert=True)
        return

    await deactivate_user(
        telegram_id=telegram_id,
        admin_telegram_id=callback.from_user.id
    )

    user = await get_user_by_telegram_id(telegram_id)
    summary = await get_user_tickets_summary(telegram_id)

    await callback.message.answer(
        f"🚫 Пользователь <code>{telegram_id}</code> отключён."
    )

    await callback.message.answer(
        format_user_card(user, summary),
        reply_markup=user_admin_keyboard(
            user_id=telegram_id,
            is_active=False
        )
    )

    try:
        await callback.bot.send_message(
            telegram_id,
            "🚫 Твой доступ к боту отключён администратором."
        )
    except Exception:
        logger.exception("Не удалось отправить административное уведомление пользователю %s", telegram_id)

    await callback.answer()


@router.callback_query(F.data.startswith("restore_user:"))
async def callback_restore_user(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    telegram_id = int(callback.data.split(":")[1])

    await restore_user(
        telegram_id=telegram_id,
        admin_telegram_id=callback.from_user.id
    )

    user = await get_user_by_telegram_id(telegram_id)
    summary = await get_user_tickets_summary(telegram_id)

    await callback.message.answer(
        f"✅ Пользователь <code>{telegram_id}</code> восстановлен."
    )

    await callback.message.answer(
        format_user_card(user, summary),
        reply_markup=user_admin_keyboard(
            user_id=telegram_id,
            is_active=True
        )
    )

    try:
        await callback.bot.send_message(
            telegram_id,
            "✅ Твой доступ к боту восстановлен."
        )
    except Exception:
        logger.exception("Не удалось отправить административное уведомление пользователю %s", telegram_id)

    await callback.answer()


@router.callback_query(F.data.startswith("change_role:"))
async def callback_change_role(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    telegram_id = int(callback.data.split(":")[1])

    await callback.message.answer(
        f"Выбери новую роль для пользователя <code>{telegram_id}</code>:",
        reply_markup=change_role_keyboard(telegram_id)
    )

    await callback.answer()


@router.callback_query(F.data.startswith("set_role:"))
async def callback_set_role(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    _, telegram_id_raw, role = callback.data.split(":")
    telegram_id = int(telegram_id_raw)

    await set_user_role(
        telegram_id=telegram_id,
        role=role,
        admin_telegram_id=callback.from_user.id
    )

    user = await get_user_by_telegram_id(telegram_id)
    summary = await get_user_tickets_summary(telegram_id)

    await callback.message.answer(
        f"🔄 Пользователю <code>{telegram_id}</code> назначена роль: <b>{role_title(role)}</b>."
    )

    await callback.message.answer(
        format_user_card(user, summary),
        reply_markup=user_admin_keyboard(
            user_id=telegram_id,
            is_active=user["is_active"] == 1
        )
    )

    try:
        await callback.bot.send_message(
            telegram_id,
            f"🔄 Твоя роль изменена.\nНовая роль: <b>{role_title(role)}</b>"
        )
    except Exception:
        logger.exception("Не удалось отправить административное уведомление пользователю %s", telegram_id)

    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_tickets:"))
async def callback_admin_user_tickets(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    telegram_id = int(callback.data.split(":")[1])
    tickets = await get_tickets_by_user_admin(telegram_id)

    if not tickets:
        await callback.message.answer(
            f"У пользователя <code>{telegram_id}</code> пока нет тикетов.",
            reply_markup=admin_users_menu()
        )
        await callback.answer()
        return

    text = f"<b>Тикеты пользователя <code>{telegram_id}</code>:</b>\n\n"

    for ticket in tickets:
        text += format_ticket_short(ticket) + "\n"

    await callback.message.answer(
        text,
        reply_markup=admin_tickets_list_keyboard(tickets)
    )

    await callback.answer()


@router.callback_query(F.data == "admin_tickets")
async def callback_admin_tickets(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    if pc_ticket_workspace_enabled():
        await send_ui_text(
            callback.bot,
            chat_id=callback.from_user.id,
            text="🎫 <b>Тикеты</b>\n\nВыбери список:",
            reply_markup=admin_tickets_menu(),
        )
    else:
        await callback.message.answer(
            "🎫 <b>Тикеты</b>\n\nВыбери список:",
            reply_markup=admin_tickets_menu(),
        )

    await callback.answer()


@router.callback_query(F.data.startswith("admin_tickets:"))
async def callback_admin_tickets_by_status(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    parts = callback.data.split(":")
    ticket_filter = normalize_admin_ticket_filter(parts[1] if len(parts) > 1 else "all")

    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except Exception:
        page = 0

    await send_admin_tickets_page(callback, ticket_filter=ticket_filter, page=page)


@router.callback_query(F.data.startswith("admin_ticket_open:"))
async def callback_admin_ticket_open(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    ticket_id = int(callback.data.split(":")[1])
    ticket = await get_ticket_by_id_admin(ticket_id)

    if not ticket:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    if pc_ticket_workspace_enabled():
        await send_ui_text(
            callback.bot,
            chat_id=callback.from_user.id,
            text=format_admin_ticket_card(ticket),
            reply_markup=admin_ticket_action_keyboard(ticket),
        )
    else:
        await callback.message.answer(
            format_admin_ticket_card(ticket),
            reply_markup=admin_ticket_action_keyboard(ticket),
        )

    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_delete:"))
async def callback_admin_ticket_delete(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    ticket_id = int(callback.data.split(":")[1])
    ticket = await get_ticket_by_id_admin(ticket_id)

    if not ticket:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    if ticket["is_deleted"] == 1:
        await callback.answer("Тикет уже удалён.", show_alert=True)
        return

    await soft_delete_ticket(
        ticket_id=ticket_id,
        admin_telegram_id=callback.from_user.id
    )

    ticket = await get_ticket_by_id_admin(ticket_id)

    await callback.message.answer(
        f"🗑 Тикет #{ticket_id} мягко удалён."
    )

    await callback.message.answer(
        format_admin_ticket_card(ticket),
        reply_markup=admin_ticket_action_keyboard(ticket)
    )

    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_restore:"))
async def callback_admin_ticket_restore(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    ticket_id = int(callback.data.split(":")[1])
    ticket = await get_ticket_by_id_admin(ticket_id)

    if not ticket:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    if ticket["is_deleted"] == 0:
        await callback.answer("Тикет уже восстановлен.", show_alert=True)
        return

    await restore_ticket(
        ticket_id=ticket_id,
        admin_telegram_id=callback.from_user.id
    )

    ticket = await get_ticket_by_id_admin(ticket_id)

    await callback.message.answer(
        f"♻️ Тикет #{ticket_id} восстановлен."
    )

    await callback.message.answer(
        format_admin_ticket_card(ticket),
        reply_markup=admin_ticket_action_keyboard(ticket)
    )

    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_status:"))
async def callback_admin_ticket_status(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    ticket_id = int(callback.data.split(":")[1])

    await callback.message.answer(
        f"Выбери новый статус для тикета #{ticket_id}:",
        reply_markup=admin_ticket_status_keyboard(ticket_id)
    )

    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_set_status:"))
async def callback_admin_ticket_set_status(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    _, ticket_id_raw, status = callback.data.split(":")
    ticket_id = int(ticket_id_raw)

    await set_ticket_status_admin(
        ticket_id=ticket_id,
        status=status,
        admin_telegram_id=callback.from_user.id
    )

    ticket = await get_ticket_by_id_admin(ticket_id)

    await callback.message.answer(
        f"🔄 Статус тикета #{ticket_id} изменён на: <b>{ticket_status_title(status)}</b>."
    )

    await callback.message.answer(
        format_admin_ticket_card(ticket),
        reply_markup=admin_ticket_action_keyboard(ticket)
    )

    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return

    stats = await get_admin_ticket_stats()
    current_reminder_time = await get_setting("reminder_time", settings.reminder_time)

    await callback.message.answer(
        "<b>📊 Мини-статистика</b>\n\n"
        f"Всего тикетов в базе: {stats['total'] or 0}\n"
        f"Видимых тикетов: {stats['visible_total'] or 0}\n"
        f"Удалённых тикетов: {stats['deleted_total'] or 0}\n"
        f"Открытых тикетов: {stats['open_total'] or 0}\n"
        f"Закрытых тикетов: {stats['closed_total'] or 0}\n"
        f"Тикетов в закупку: {stats['purchasing_total'] or 0}\n"
        f"Тикетов в клиентский отдел: {stats['client_total'] or 0}\n\n"
        f"Время ежедневных напоминаний: <b>{current_reminder_time}</b>",
        reply_markup=admin_menu()
    )

    await callback.answer()


@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery):
    user = await get_user_by_telegram_id(callback.from_user.id)
    is_admin = is_admin_user(callback.from_user.id)

    menu_text = await build_main_menu_text(callback.from_user.id, user["role"] if user else None)
    await send_ui_text(
        callback.bot,
        chat_id=callback.from_user.id,
        text=menu_text,
        reply_markup=main_menu_for_role(user["role"], is_admin=is_admin) if user else None,
    )

    await callback.answer()

def _note_text(note) -> str:
    status = "✅ Выполнено" if note["status"] == "done" else "📝 Запланировано"
    return (
        f"<b>Заметка #{note['id']}</b>\n\n"
        f"<b>{html_escape(note['title'])}</b>\n"
        f"Статус: {status}\n\n"
        f"{html_escape(note['body'])}"
    )


@router.callback_query(F.data == "admin_notes")
async def callback_admin_notes(callback: CallbackQuery, state: FSMContext):
    if await deny_if_not_admin(callback):
        return
    await state.clear()
    notes = await list_notes()
    await callback.message.answer(
        "📝 <b>Заметки об обновлениях</b>\n\nЗдесь можно хранить идеи и планы следующих версий.",
        reply_markup=admin_notes_keyboard(notes),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_note_add")
async def callback_admin_note_add(callback: CallbackQuery, state: FSMContext):
    if await deny_if_not_admin(callback):
        return
    await state.clear()
    await state.set_state(AdminStates.waiting_note_title)
    await callback.message.answer("Отправь короткое название заметки.")
    await callback.answer()


@router.message(AdminStates.waiting_note_title)
async def process_admin_note_title(message: Message, state: FSMContext):
    if await deny_if_not_admin(message):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не может быть пустым.")
        return
    await state.update_data(note_title=title[:200])
    await state.set_state(AdminStates.waiting_note_body)
    await message.answer("Теперь отправь описание идеи или обновления.")


@router.message(AdminStates.waiting_note_body)
async def process_admin_note_body(message: Message, state: FSMContext):
    if await deny_if_not_admin(message):
        return
    body = (message.text or "").strip()
    if not body:
        await message.answer("Текст заметки не может быть пустым.")
        return
    data = await state.get_data()
    note_id = await create_note(data["note_title"], body, message.from_user.id)
    await state.clear()
    note = await get_note(note_id)
    await message.answer("✅ Заметка сохранена.")
    await message.answer(_note_text(note), reply_markup=admin_note_card_keyboard(note_id, note["status"]))


@router.callback_query(F.data.startswith("admin_note:"))
async def callback_admin_note_card(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    note_id = int(callback.data.split(":")[1])
    note = await get_note(note_id)
    if not note:
        await callback.answer("Заметка не найдена.", show_alert=True)
        return
    await callback.message.answer(_note_text(note), reply_markup=admin_note_card_keyboard(note_id, note["status"]))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_note_edit_title:"))
async def callback_admin_note_edit_title(callback: CallbackQuery, state: FSMContext):
    if await deny_if_not_admin(callback):
        return
    note_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(note_id=note_id)
    await state.set_state(AdminStates.waiting_note_edit_title)
    await callback.message.answer("Отправь новое название заметки.")
    await callback.answer()


@router.message(AdminStates.waiting_note_edit_title)
async def process_admin_note_edit_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не может быть пустым.")
        return
    data = await state.get_data()
    await update_note(int(data["note_id"]), title=title[:200])
    await state.clear()
    await message.answer("✅ Название обновлено.")


@router.callback_query(F.data.startswith("admin_note_edit_body:"))
async def callback_admin_note_edit_body(callback: CallbackQuery, state: FSMContext):
    if await deny_if_not_admin(callback):
        return
    note_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(note_id=note_id)
    await state.set_state(AdminStates.waiting_note_edit_body)
    await callback.message.answer("Отправь новый текст заметки.")
    await callback.answer()


@router.message(AdminStates.waiting_note_edit_body)
async def process_admin_note_edit_body(message: Message, state: FSMContext):
    body = (message.text or "").strip()
    if not body:
        await message.answer("Текст не может быть пустым.")
        return
    data = await state.get_data()
    await update_note(int(data["note_id"]), body=body)
    await state.clear()
    await message.answer("✅ Текст заметки обновлён.")


@router.callback_query(F.data.startswith("admin_note_toggle:"))
async def callback_admin_note_toggle(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    note_id = int(callback.data.split(":")[1])
    note = await get_note(note_id)
    if not note:
        await callback.answer("Заметка не найдена.", show_alert=True)
        return
    new_status = "planned" if note["status"] == "done" else "done"
    await update_note(note_id, status=new_status)
    await callback.answer("Статус изменён.")
    updated = await get_note(note_id)
    await callback.message.answer(_note_text(updated), reply_markup=admin_note_card_keyboard(note_id, new_status))


@router.callback_query(F.data.startswith("admin_note_delete:"))
async def callback_admin_note_delete(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    note_id = int(callback.data.split(":")[1])
    await delete_note(note_id)
    await callback.message.answer("🗑 Заметка удалена.", reply_markup=admin_notes_keyboard(await list_notes()))
    await callback.answer()


@router.callback_query(F.data == "admin_notes_export")
async def callback_admin_notes_export(callback: CallbackQuery):
    if await deny_if_not_admin(callback):
        return
    notes = await list_notes()
    lines = ["ЗАМЕТКИ ОБ ОБНОВЛЕНИЯХ", "=" * 30, ""]
    for note in notes:
        status = "ВЫПОЛНЕНО" if note["status"] == "done" else "ЗАПЛАНИРОВАНО"
        lines.extend([f"#{note['id']} [{status}] {note['title']}", str(note['body']), "", "-" * 30, ""])
    if not notes:
        lines.append("Заметок пока нет.")
    content = "\n".join(lines).encode("utf-8")
    await callback.message.answer_document(
        BufferedInputFile(content, filename="batyabot2_update_notes.txt"),
        caption="📥 Экспорт заметок об обновлениях",
    )
    await callback.answer()
