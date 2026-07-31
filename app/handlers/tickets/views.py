import logging

from aiogram.types import CallbackQuery

from app.keyboards.common import (
    bottom_menu_for_role,
    department_by_role,
    is_observer_role,
    main_menu_for_role,
)
from app.keyboards.observer import (
    observer_stats_menu_keyboard,
    observer_tickets_keyboard,
)
from app.keyboards.tickets import (
    archive_menu_keyboard,
    archive_tickets_keyboard,
    overdue_tickets_keyboard,
    ticket_action_keyboard,
    tickets_list_keyboard,
)
from app.services.attachments import get_ticket_attachments
from app.services.preferences import user_text
from app.services.work_management import mark_ticket_read
from app.services.tickets import (
    get_archive_incoming_tickets,
    get_archive_outgoing_tickets,
    get_observer_active_tickets,
    get_observer_closed_tickets,
    get_overdue_client_tickets,
    get_ticket_comments,
    get_ticket_events,
)
from app.utils import format_moscow_datetime, html_escape

from .utils import (
    answer_long,
    get_author_name_from_ticket,
    get_current_user_and_admin,
    get_department_name,
    get_status_name,
    has_text_value,
    is_closed_status,
    optional_line,
    row_get,
    short_text,
    text_or_dash,
)

logger = logging.getLogger(__name__)

async def show_main_menu(message_or_call, user=None, admin_flag: bool = False):
    if user is None:
        telegram_id = message_or_call.from_user.id
        user, admin_flag = await get_current_user_and_admin(telegram_id)

    telegram_id = int(message_or_call.from_user.id)
    text = "Главное меню."
    action_prompt = await user_text(telegram_id, "main_menu_title")

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.answer(
            text,
            reply_markup=bottom_menu_for_role(
                role=row_get(user, "role"),
                is_admin=admin_flag,
            )
        )

        await message_or_call.message.answer(
            action_prompt,
            reply_markup=main_menu_for_role(
                role=row_get(user, "role"),
                is_admin=admin_flag,
            )
        )

        await message_or_call.answer()
    else:
        await message_or_call.answer(
            text,
            reply_markup=bottom_menu_for_role(
                role=row_get(user, "role"),
                is_admin=admin_flag,
            )
        )

        await message_or_call.answer(
            action_prompt,
            reply_markup=main_menu_for_role(
                role=row_get(user, "role"),
                is_admin=admin_flag,
            )
        )

async def send_ticket_attachments(message_or_call, ticket_id: int, attachments):
    if not attachments:
        return

    chat_id = message_or_call.from_user.id
    bot = message_or_call.bot

    for attachment in attachments:
        file_id = row_get(attachment, "file_id")
        file_type = row_get(attachment, "file_type")

        if not file_id:
            continue

        caption = f"📎 Вложение к тикету #{ticket_id}"

        try:
            if file_type == "photo":
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=caption
                )
            elif file_type == "document":
                await bot.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=caption
                )
            elif file_type == "video":
                await bot.send_video(
                    chat_id=chat_id,
                    video=file_id,
                    caption=caption
                )
        except Exception:
            logger.exception("Не удалось отправить вложение тикета %s", ticket_id)

async def send_ticket_card(message_or_call, ticket, user=None, admin_flag: bool = False):
    if not ticket:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Тикет не найден.", show_alert=True)
        else:
            await message_or_call.answer("Тикет не найден.")
        return

    if user is None:
        telegram_id = message_or_call.from_user.id
        user, admin_flag = await get_current_user_and_admin(telegram_id)

    comments = await get_ticket_comments(int(ticket["id"]), limit=20)
    attachments = await get_ticket_attachments(int(ticket["id"]))

    comments_text = ""

    if comments:
        comments_lines = []

        for comment in comments:
            author_name = (
                row_get(comment, "author_name")
                or row_get(comment, "author_username")
                or row_get(comment, "author_telegram_id")
            )

            comments_lines.append(
                f"— {html_escape(author_name)} [{format_moscow_datetime(row_get(comment, 'created_at'))}]:\n{html_escape(row_get(comment, 'text'))}"
            )

        comments_text = "\n\n💬 Комментарии:\n" + "\n\n".join(comments_lines)

    attachments_text = ""

    if attachments:
        attachments_text = f"\n\n📎 Вложения: {len(attachments)} шт. Они будут отправлены отдельными сообщениями ниже."

    order_line = optional_line("🔢 Заказ: ", row_get(ticket, "order_number"))
    closed_ticket = is_closed_status(row_get(ticket, "status"))
    closed_line = (
        f"🏁 Закрыт/отменён: {format_moscow_datetime(row_get(ticket, 'closed_at'))}\n"
        if closed_ticket and has_text_value(row_get(ticket, "closed_at"))
        else ""
    )

    priority_names = {
        "important": "🟡 Важный",
        "urgent": "🔴 Срочный",
    }
    category_names = {
        "question": "Вопрос",
        "task": "Задача",
        "problem": "Проблема",
        "documents": "Документы",
    }

    # Обычный приоритет является значением по умолчанию и не перегружает карточку.
    # Строка появляется только для явно значимых приоритетов.
    priority_line = optional_line(
        "🚦 Приоритет: ",
        priority_names.get(row_get(ticket, "priority")),
    )
    category_line = optional_line(
        "🏷 Тип: ",
        category_names.get(row_get(ticket, "category")),
    )

    assignee_name = row_get(ticket, "assignee_full_name") or row_get(ticket, "assignee_username")
    if not assignee_name and row_get(ticket, "taken_by"):
        assignee_name = row_get(ticket, "taken_by")
    assignee_line = (
        f"👤 Исполнитель: {html_escape(assignee_name)}\n"
        if assignee_name
        else "👥 Исполнитель: общий тикет отдела\n"
    )
    summary_text = ""
    if has_text_value(row_get(ticket, "current_summary")):
        summary_text += f"\n\n📍 <b>Текущий итог:</b>\n{html_escape(row_get(ticket, 'current_summary'))}"
    if has_text_value(row_get(ticket, "next_action")):
        summary_text += f"\n\n➡️ <b>Следующее действие:</b>\n{html_escape(row_get(ticket, 'next_action'))}"
    snooze_line = (
        f"⏰ Отложен до: {format_moscow_datetime(row_get(ticket, 'snoozed_until'))}\n"
        if has_text_value(row_get(ticket, "snoozed_until"))
        else ""
    )

    order_status_snapshot_text = ""
    viewer_department = department_by_role(row_get(user, "role"))
    if (
        has_text_value(row_get(ticket, "order_status_snapshot"))
        and (admin_flag or viewer_department == "purchasing")
    ):
        order_status_snapshot_text = (
            "\n\n📦 <b>Статус заказа на момент создания тикета:</b>\n"
            + html_escape(row_get(ticket, "order_status_snapshot"))
        )

    archive_details = ""
    if closed_ticket:
        archive_details = (
            f"👤 Автор: {get_author_name_from_ticket(ticket)}\n"
            f"🏢 От кого: {get_department_name(row_get(ticket, 'requester_department'))}\n"
            f"🎯 Кому: {get_department_name(row_get(ticket, 'executor_department'))}\n"
            f"🔄 Обновлён: {format_moscow_datetime(row_get(ticket, 'updated_at'))}\n"
        )

    history_text = ""
    if is_closed_status(row_get(ticket, "status")):
        events = await get_ticket_events(int(ticket["id"]), limit=100)
        if events:
            event_names = {
                "created": "Тикет создан", "taken": "Взят в работу",
                "comment": "Добавлен комментарий", "status_changed": "Изменён статус",
                "priority_changed": "Изменён приоритет", "category_changed": "Изменён тип",
            }
            lines = []
            for event in events:
                actor = html_escape(row_get(event, "actor_name") or row_get(event, "actor_username") or row_get(event, "actor_telegram_id") or "Система")
                label = event_names.get(row_get(event, "event_type"), html_escape(row_get(event, "event_type")))
                details = row_get(event, "details")
                detail_part = f" — {html_escape(details)}" if has_text_value(details) else ""
                lines.append(f"— {format_moscow_datetime(row_get(event, 'created_at'))}: {label} ({actor}){detail_part}")
            history_text = "\n\n🕓 История действий:\n" + "\n".join(lines)

    text = (
        f"🎫 Тикет #{ticket['id']}\n\n"
        f"📌 Статус: {get_status_name(row_get(ticket, 'status'))}\n"
        f"{assignee_line}"
        f"{snooze_line}"
        f"{priority_line}"
        f"{category_line}"
        f"{order_line}"
        f"{archive_details}"
        f"🕒 Создан: {format_moscow_datetime(row_get(ticket, 'created_at'))}\n"
        f"{closed_line}\n"
        f"📝 Описание:\n{html_escape(row_get(ticket, 'description'))}"
        f"{order_status_snapshot_text}"
        f"{summary_text}"
        f"{attachments_text}"
        f"{comments_text}"
        f"{history_text}"
    )

    keyboard = ticket_action_keyboard(
        ticket=ticket,
        user=user,
        is_admin=admin_flag,
    )

    await answer_long(message_or_call, text, reply_markup=keyboard)
    try:
        await mark_ticket_read(int(ticket["id"]), int(message_or_call.from_user.id))
    except Exception:
        logger.exception("Не удалось отметить тикет %s прочитанным", ticket["id"])

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

    if attachments:
        await send_ticket_attachments(
            message_or_call=message_or_call,
            ticket_id=int(ticket["id"]),
            attachments=attachments,
        )

async def send_tickets_list(message_or_call, title: str, tickets):
    if not tickets:
        empty_text = await user_text(message_or_call.from_user.id, "no_tickets")
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(f"{title}\n\n{empty_text}")
            await message_or_call.answer()
        else:
            await message_or_call.answer(f"{title}\n\n{empty_text}")
        return

    lines = [title, ""]

    for ticket in tickets:
        order_line = optional_line("🔢 Заказ: ", row_get(ticket, "order_number"))
        attachments = await get_ticket_attachments(int(ticket["id"]))
        attachments_line = f"📎 Вложения: {len(attachments)} шт.\n" if attachments else ""

        lines.append(
            f"#{ticket['id']} — {get_status_name(row_get(ticket, 'status'))}\n"
            f"{order_line}"
            f"{attachments_line}"
            f"👤 Автор: {get_author_name_from_ticket(ticket)}\n"
            f"📝 {short_text(row_get(ticket, 'description'), 1000)}"
        )

    text = "\n\n".join(lines)
    title_lower = title.lower()
    list_type = None
    if "исход" in title_lower:
        list_type = "outgoing"
    elif "вход" in title_lower:
        list_type = "incoming"
    elif "работ" in title_lower:
        list_type = "work"
    elif "архив" in title_lower:
        list_type = "archive"
    keyboard = tickets_list_keyboard(tickets, list_type=list_type)

    if isinstance(message_or_call, CallbackQuery):
        await answer_long(message_or_call, text, reply_markup=keyboard)
        await message_or_call.answer()
    else:
        await answer_long(message_or_call, text, reply_markup=keyboard)

async def send_archive_menu(message_or_call):
    text = "📦 Архив тикетов\n\nВыбери, какой архив открыть."

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.answer(text, reply_markup=archive_menu_keyboard())
        await message_or_call.answer()
    else:
        await message_or_call.answer(text, reply_markup=archive_menu_keyboard())

def build_archive_text(tickets, title: str, page: int = 0, page_size: int = 10) -> str:
    total = len(tickets)
    total_pages = (total + page_size - 1) // page_size if total else 1

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    start = page * page_size
    end = start + page_size
    page_tickets = tickets[start:end]

    lines = [title, ""]

    if total_pages > 1:
        lines.append(f"Страница {page + 1} из {total_pages}. Всего тикетов: {total}")
        lines.append("")

    for ticket in page_tickets:
        order_line = optional_line("🔢 Заказ: ", row_get(ticket, "order_number"))

        lines.append(
            f"#{ticket['id']} — {get_status_name(row_get(ticket, 'status'))}\n"
            f"{order_line}"
            f"👤 Автор: {get_author_name_from_ticket(ticket)}\n"
            f"📝 {short_text(row_get(ticket, 'description'), 700)}"
        )

    return "\n\n".join(lines)

async def send_archive_page(message_or_call, archive_type: str, page: int = 0):
    user, admin_flag = await get_current_user_and_admin(message_or_call.from_user.id)

    if not user:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Нет доступа.", show_alert=True)
        else:
            await message_or_call.answer("Нет доступа.")
        return

    department = department_by_role(row_get(user, "role"))

    if archive_type == "incoming":
        title = "📥 Архив входящих"
        tickets = await get_archive_incoming_tickets(
            department=None if admin_flag else department,
            limit=None,
        )
    elif archive_type == "outgoing":
        title = "📤 Архив исходящих"
        tickets = await get_archive_outgoing_tickets(
            telegram_id=None if admin_flag else message_or_call.from_user.id,
            limit=None,
        )
    else:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Неизвестный архив.", show_alert=True)
        else:
            await message_or_call.answer("Неизвестный архив.")
        return

    if not tickets:
        empty_text = await user_text(message_or_call.from_user.id, "no_archive_tickets")
        text = f"{title}\n\n{empty_text}"

        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(text, reply_markup=archive_menu_keyboard())
            await message_or_call.answer()
        else:
            await message_or_call.answer(text, reply_markup=archive_menu_keyboard())

        return

    total_pages = (len(tickets) + 10 - 1) // 10

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = build_archive_text(tickets, title, page=page, page_size=10)
    keyboard = archive_tickets_keyboard(tickets, archive_type=archive_type, page=page, page_size=10)

    if isinstance(message_or_call, CallbackQuery):
        await answer_long(message_or_call, text, reply_markup=keyboard)
        await message_or_call.answer()
    else:
        await answer_long(message_or_call, text, reply_markup=keyboard)

def build_overdue_text(tickets, page: int = 0, page_size: int = 10) -> str:
    total = len(tickets)
    total_pages = (total + page_size - 1) // page_size if total else 1

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    start = page * page_size
    end = start + page_size
    page_tickets = tickets[start:end]

    warning_tickets = []
    urgent_tickets = []

    for ticket in page_tickets:
        overdue_days = int(row_get(ticket, "overdue_days", 0) or 0)

        if overdue_days >= 4:
            urgent_tickets.append(ticket)
        else:
            warning_tickets.append(ticket)

    text = (
        "⏰ <b>Просроченные тикеты</b>\n\n"
        "Показаны тикеты от отдела закупки, которые сейчас у клиентского отдела.\n"
        "Возраст считается от даты последнего обновления.\n\n"
    )

    if total_pages > 1:
        text += f"Страница {page + 1} из {total_pages}. Всего тикетов: {total}\n\n"

    if warning_tickets:
        text += "⚠️ <b>Открыт более 2 дней:</b>\n"

        for ticket in warning_tickets:
            order_number = row_get(ticket, "order_number")
            order_part = ""

            if has_text_value(order_number):
                order_part = f" Заказ: {html_escape(str(order_number).strip())} —"

            overdue_days = int(row_get(ticket, "overdue_days", 0) or 0)
            description = short_text(row_get(ticket, "description"), 300)

            text += f"#{ticket['id']}{order_part} {description} ({overdue_days} дн.)\n"

        text += "\n"

    if urgent_tickets:
        text += "🚨 <b>Открыт более 4 дней, срочно обработать:</b>\n"

        for ticket in urgent_tickets:
            order_number = row_get(ticket, "order_number")
            order_part = ""

            if has_text_value(order_number):
                order_part = f" Заказ: {html_escape(str(order_number).strip())} —"

            overdue_days = int(row_get(ticket, "overdue_days", 0) or 0)
            description = short_text(row_get(ticket, "description"), 300)

            text += f"#{ticket['id']}{order_part} {description} ({overdue_days} дн.)\n"

        text += "\n"

    text += "Открой нужный тикет кнопкой ниже."

    return text

async def send_overdue_page(message_or_call, page: int = 0):
    tickets = await get_overdue_client_tickets()

    if not tickets:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer("Просроченных тикетов сейчас нет.")
            await message_or_call.answer()
        else:
            await message_or_call.answer("Просроченных тикетов сейчас нет.")
        return

    total_pages = (len(tickets) + 10 - 1) // 10

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = build_overdue_text(tickets, page=page, page_size=10)
    keyboard = overdue_tickets_keyboard(tickets, page=page, page_size=10)

    if isinstance(message_or_call, CallbackQuery):
        await answer_long(message_or_call, text, reply_markup=keyboard)
        await message_or_call.answer()
    else:
        await answer_long(message_or_call, text, reply_markup=keyboard)

def build_observer_tickets_text(tickets, title: str, page: int = 0, page_size: int = 10) -> str:
    total = len(tickets)
    total_pages = (total + page_size - 1) // page_size if total else 1

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    start = page * page_size
    end = start + page_size
    page_tickets = tickets[start:end]

    lines = [title, ""]

    lines.append(f"Страница {page + 1} из {total_pages}. Всего тикетов: {total}")
    lines.append("")

    for ticket in page_tickets:
        order_line = optional_line("🔢 Заказ: ", row_get(ticket, "order_number"))

        lines.append(
            f"#{ticket['id']} — {get_status_name(row_get(ticket, 'status'))}\n"
            f"{order_line}"
            f"👤 Автор: {get_author_name_from_ticket(ticket)}\n"
            f"🏢 От кого: {get_department_name(row_get(ticket, 'requester_department'))}\n"
            f"🎯 Кому: {get_department_name(row_get(ticket, 'executor_department'))}\n"
            f"📝 {short_text(row_get(ticket, 'description'), 700)}"
        )

    return "\n\n".join(lines)

async def send_observer_tickets_page(message_or_call, list_type: str, page: int = 0):
    user, admin_flag = await get_current_user_and_admin(message_or_call.from_user.id)

    if not user or not is_observer_role(row_get(user, "role")):
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Нет доступа.", show_alert=True)
        else:
            await message_or_call.answer("Нет доступа.")
        return

    if list_type == "active":
        tickets = await get_observer_active_tickets()
        title = "🟢 Активные тикеты"
    elif list_type == "closed":
        tickets = await get_observer_closed_tickets()
        title = "✅ Закрытые и отменённые тикеты"
    else:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Неизвестный список.", show_alert=True)
        else:
            await message_or_call.answer("Неизвестный список.")
        return

    if not tickets:
        empty_text = await user_text(message_or_call.from_user.id, "no_tickets")
        text = f"{title}\n\n{empty_text}"

        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(text, reply_markup=main_menu_for_role(row_get(user, "role")))
            await message_or_call.answer()
        else:
            await message_or_call.answer(text, reply_markup=main_menu_for_role(row_get(user, "role")))

        return

    total_pages = (len(tickets) + 10 - 1) // 10

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = build_observer_tickets_text(tickets, title, page=page, page_size=10)
    keyboard = observer_tickets_keyboard(tickets, list_type=list_type, page=page, page_size=10)

    if isinstance(message_or_call, CallbackQuery):
        await answer_long(message_or_call, text, reply_markup=keyboard)
        await message_or_call.answer()
    else:
        await answer_long(message_or_call, text, reply_markup=keyboard)

def format_minutes(value) -> str:
    if value is None:
        return "—"

    try:
        minutes = int(round(float(value)))
    except Exception:
        return "—"

    if minutes < 60:
        return f"{minutes} мин."

    hours = minutes // 60
    rest_minutes = minutes % 60

    if hours < 24:
        if rest_minutes:
            return f"{hours} ч. {rest_minutes} мин."
        return f"{hours} ч."

    days = hours // 24
    rest_hours = hours % 24

    if rest_hours:
        return f"{days} дн. {rest_hours} ч."

    return f"{days} дн."

def build_observer_report_text(report, title: str) -> str:
    total_created = row_get(report, "total_created", 0) or 0
    open_total = row_get(report, "open_total", 0) or 0
    done_total = row_get(report, "done_total", 0) or 0
    cancelled_total = row_get(report, "cancelled_total", 0) or 0
    avg_minutes = row_get(report, "avg_minutes")

    return (
        f"📊 <b>{html_escape(title)}</b>\n\n"
        f"📌 Открыто тикетов: {total_created}\n"
        f"🟢 Сейчас активных: {open_total}\n"
        f"🏁 Выполнено/закрыто: {done_total}\n"
        f"❌ Отменено: {cancelled_total}\n"
        f"⏱ Среднее время выполнения: {format_minutes(avg_minutes)}\n\n"
        f"Среднее время считается только по тикетам со статусом done/cancelled "
        f"от времени создания тикета до времени закрытия/отмены."
    )

async def send_observer_stats_menu(message_or_call):
    user, admin_flag = await get_current_user_and_admin(message_or_call.from_user.id)

    if not user or not is_observer_role(row_get(user, "role")):
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Нет доступа.", show_alert=True)
        else:
            await message_or_call.answer("Нет доступа.")
        return

    text = "📊 Статистика\n\nВыбери тип отчёта."

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.answer(text, reply_markup=observer_stats_menu_keyboard())
        await message_or_call.answer()
    else:
        await message_or_call.answer(text, reply_markup=observer_stats_menu_keyboard())
