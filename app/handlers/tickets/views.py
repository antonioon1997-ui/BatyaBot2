import logging

from aiogram.types import CallbackQuery

from app.keyboards.common import (
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
from app.services.ticket_messages import delete_message_ids, replace_ticket_message_bundle, send_live_ticket_text
from app.services.ui_messages import send_ui_text
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
    action_prompt = await user_text(telegram_id, "main_menu_title")
    await send_ui_text(
        message_or_call.bot,
        chat_id=telegram_id,
        text=f"🏠 <b>Главное меню</b>\n\n{action_prompt}",
        reply_markup=main_menu_for_role(
            role=row_get(user, "role"),
            is_admin=admin_flag,
        ),
    )
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()


async def _send_primary_ui(message_or_call, text: str, reply_markup=None) -> None:
    await send_ui_text(
        message_or_call.bot,
        chat_id=int(message_or_call.from_user.id),
        text=text,
        reply_markup=reply_markup,
    )


async def _send_text_collect(bot, chat_id: int, text: str, reply_markup=None) -> list[int]:
    from .utils import split_long_text

    chunks = split_long_text(text)
    sent_ids: list[int] = []
    for index, chunk in enumerate(chunks):
        message = await bot.send_message(
            chat_id=chat_id,
            text=chunk,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
        )
        sent_ids.append(int(message.message_id))
    return sent_ids


def _ticket_media_caption(ticket, comments, attachment_count: int) -> str:
    """Короткая HTML-подпись, гарантированно помещающаяся под вложением."""
    lines = [
        f"🎫 <b>Тикет #{int(row_get(ticket, 'id'))}</b>",
        f"📌 Статус: {get_status_name(row_get(ticket, 'status'))}",
    ]
    assignee = row_get(ticket, "assignee_full_name") or row_get(ticket, "assignee_username")
    if assignee:
        lines.append(f"👤 Исполнитель: {html_escape(str(assignee))}")
    order_number = row_get(ticket, "order_number")
    if has_text_value(order_number):
        lines.append(f"🔢 Заказ: {html_escape(str(order_number))}")
    lines.append(f"🕒 Создан: {format_moscow_datetime(row_get(ticket, 'created_at'))}")
    lines.append("")
    lines.append(f"📝 {short_text(row_get(ticket, 'description'), 300)}")
    if comments:
        latest = comments[-1]
        author = row_get(latest, "author_name") or row_get(latest, "author_username") or row_get(latest, "author_telegram_id")
        lines.append("")
        lines.append(f"💬 Последнее дополнение — {html_escape(str(author))}:")
        lines.append(short_text(row_get(latest, "text"), 160))
    if attachment_count > 1:
        lines.append("")
        lines.append(f"📎 Вложений: {attachment_count}")
    return "\n".join(lines)


async def _send_ticket_attachments_to_chat(
    bot,
    chat_id: int,
    ticket_id: int,
    attachments,
    *,
    primary_caption: str,
    primary_markup=None,
) -> list[int]:
    """Отправляет вложения как часть живой карточки тикета в конкретный чат."""
    if not attachments:
        return []

    sent_ids: list[int] = []

    for index, attachment in enumerate(attachments):
        file_id = row_get(attachment, "file_id")
        file_type = row_get(attachment, "file_type")
        if not file_id:
            continue

        caption = primary_caption if index == 0 else f"📎 Вложение {index + 1} к тикету #{ticket_id}"
        markup = primary_markup if index == 0 else None
        try:
            if file_type == "photo":
                message = await bot.send_photo(chat_id=int(chat_id), photo=file_id, caption=caption, reply_markup=markup)
            elif file_type == "document":
                message = await bot.send_document(chat_id=int(chat_id), document=file_id, caption=caption, reply_markup=markup)
            elif file_type == "video":
                message = await bot.send_video(chat_id=int(chat_id), video=file_id, caption=caption, reply_markup=markup)
            else:
                continue
            sent_ids.append(int(message.message_id))
        except Exception:
            logger.exception("Не удалось отправить вложение тикета %s", ticket_id)
            if index == 0:
                raise

    return sent_ids


async def send_ticket_attachments(
    message_or_call,
    ticket_id: int,
    attachments,
    *,
    primary_caption: str,
    primary_markup=None,
) -> list[int]:
    return await _send_ticket_attachments_to_chat(
        message_or_call.bot,
        int(message_or_call.from_user.id),
        ticket_id,
        attachments,
        primary_caption=primary_caption,
        primary_markup=primary_markup,
    )

async def _build_ticket_card_payload(ticket, user, admin_flag: bool = False):
    """Собирает полную текстовую карточку тикета без отправки в Telegram.

    Карточка используется и при обычном открытии тикета, и в уведомлении о
    выполнении. Благодаря этому уведомление о завершении всегда содержит ту же
    историю ответов, которую пользователь увидел бы после ручного открытия.
    """
    comments = await get_ticket_comments(int(ticket["id"]), limit=None)
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
                f"— {html_escape(author_name)} [{format_moscow_datetime(row_get(comment, 'created_at'))}]:\n"
                f"{html_escape(row_get(comment, 'text'))}"
            )
        comments_text = "\n\n💬 Комментарии:\n" + "\n\n".join(comments_lines)

    attachments_text = ""
    if attachments:
        attachments_text = f"\n\n📎 Вложения: {len(attachments)} шт."

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
                "created": "Тикет создан",
                "taken": "Взят в работу",
                "comment": "Добавлен комментарий",
                "status_changed": "Изменён статус",
                "priority_changed": "Изменён приоритет",
                "category_changed": "Изменён тип",
            }
            lines = []
            for event in events:
                actor = html_escape(
                    row_get(event, "actor_name")
                    or row_get(event, "actor_username")
                    or row_get(event, "actor_telegram_id")
                    or "Система"
                )
                label = event_names.get(
                    row_get(event, "event_type"),
                    html_escape(row_get(event, "event_type")),
                )
                details = row_get(event, "details")
                detail_part = f" — {html_escape(details)}" if has_text_value(details) else ""
                lines.append(
                    f"— {format_moscow_datetime(row_get(event, 'created_at'))}: "
                    f"{label} ({actor}){detail_part}"
                )
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
    return text, comments, attachments, keyboard


async def send_completed_ticket_card_to_creator(bot, ticket, headline: str) -> None:
    """Отправляет автору компактное уведомление о выполнении тикета.

    В рабочем уведомлении оставляем только то, что нужно менеджеру для быстрого
    понимания результата: номер тикета, заказ BS (если есть), исходное описание
    и всю переписку. Служебные поля карточки и история действий доступны при
    обычном открытии тикета, но не перегружают уведомление о завершении.
    """
    creator_id = int(row_get(ticket, "created_by", 0) or 0)
    if not creator_id:
        return

    try:
        creator, admin_flag = await get_current_user_and_admin(creator_id)
        ticket_id = int(row_get(ticket, "id"))
        comments = await get_ticket_comments(ticket_id, limit=None)

        status = row_get(ticket, "status")
        if status == "done":
            title = f"✅ Тикет #{ticket_id}: выполнен и закрыт"
        else:
            title = f"✅ Тикет #{ticket_id}: помечен выполненным"

        lines = [title]

        order_number = row_get(ticket, "order_number")
        if has_text_value(order_number):
            lines.extend(["", f"🔢 № Заказа BS: {html_escape(order_number)}"])

        lines.extend(["", "📝 Описание:", html_escape(row_get(ticket, "description"))])

        lines.extend(["", "💬 Комментарии:"])
        if comments:
            comment_blocks = []
            for comment in comments:
                author_name = (
                    row_get(comment, "author_name")
                    or row_get(comment, "author_username")
                    or row_get(comment, "author_telegram_id")
                    or "Пользователь"
                )
                comment_blocks.append(
                    f"— {html_escape(author_name)}:\n{html_escape(row_get(comment, 'text'))}"
                )
            lines.append("\n\n".join(comment_blocks))
        else:
            lines.append("— нет")

        keyboard = ticket_action_keyboard(
            ticket=ticket,
            user=creator,
            is_admin=admin_flag,
        )

        await send_live_ticket_text(
            bot,
            chat_id=creator_id,
            ticket_id=ticket_id,
            text="\n".join(lines),
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception(
            "Не удалось отправить компактную карточку завершённого тикета %s автору %s",
            row_get(ticket, "id"),
            creator_id,
        )


async def send_ticket_card_to_user(
    bot,
    *,
    ticket,
    user,
    admin_flag: bool = False,
    mark_read: bool = False,
) -> list[int]:
    """Отправляет пользователю ту же живую карточку, что и при ручном открытии тикета.

    Используется в том числе для первого уведомления о новом входящем тикете,
    чтобы фото/документ/видео сразу были частью карточки, а не отдельной строкой
    «Есть вложения». Автоматическая доставка не помечает тикет прочитанным.
    """
    if not ticket or not user:
        return []

    chat_id = int(row_get(user, "telegram_id"))
    text, comments, attachments, keyboard = await _build_ticket_card_payload(
        ticket,
        user,
        admin_flag,
    )

    sent_ids: list[int] = []
    try:
        if attachments:
            media_caption = text if len(text) <= 900 else _ticket_media_caption(ticket, comments, len(attachments))
            sent_ids.extend(
                await _send_ticket_attachments_to_chat(
                    bot,
                    chat_id,
                    int(ticket["id"]),
                    attachments,
                    primary_caption=media_caption,
                    primary_markup=keyboard,
                )
            )
            if media_caption != text:
                sent_ids.extend(await _send_text_collect(bot, chat_id, text))
        else:
            sent_ids.extend(await _send_text_collect(bot, chat_id, text, reply_markup=keyboard))

        if not sent_ids:
            sent_ids.extend(await _send_text_collect(bot, chat_id, text, reply_markup=keyboard))

        await replace_ticket_message_bundle(
            bot,
            chat_id=chat_id,
            ticket_id=int(ticket["id"]),
            new_message_ids=sent_ids,
        )
    except Exception:
        await delete_message_ids(bot, chat_id, sent_ids)
        raise

    if mark_read:
        try:
            await mark_ticket_read(int(ticket["id"]), chat_id)
        except Exception:
            logger.exception("Не удалось отметить тикет %s прочитанным", ticket["id"])

    return sent_ids


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

    try:
        await send_ticket_card_to_user(
            message_or_call.bot,
            ticket=ticket,
            user=user,
            admin_flag=admin_flag,
            mark_read=True,
        )
    except Exception:
        logger.exception("Не удалось показать карточку тикета %s", ticket["id"])
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Не удалось открыть тикет. Попробуйте ещё раз.", show_alert=True)
        else:
            await message_or_call.answer("Не удалось открыть тикет. Попробуйте ещё раз.")
        return

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

async def send_tickets_list(message_or_call, title: str, tickets):
    if not tickets:
        empty_text = await user_text(message_or_call.from_user.id, "no_tickets")
        await _send_primary_ui(message_or_call, f"{title}\n\n{empty_text}")
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer()
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

    await _send_primary_ui(message_or_call, text, reply_markup=keyboard)
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

async def send_archive_menu(message_or_call):
    text = "📦 Архив тикетов\n\nВыбери, какой архив открыть."

    await _send_primary_ui(message_or_call, text, reply_markup=archive_menu_keyboard())
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

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

        await _send_primary_ui(message_or_call, text, reply_markup=archive_menu_keyboard())
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer()
        return

    total_pages = (len(tickets) + 10 - 1) // 10

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = build_archive_text(tickets, title, page=page, page_size=10)
    keyboard = archive_tickets_keyboard(tickets, archive_type=archive_type, page=page, page_size=10)

    await _send_primary_ui(message_or_call, text, reply_markup=keyboard)
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

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
        await _send_primary_ui(message_or_call, "Просроченных тикетов сейчас нет.")
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer()
        return

    total_pages = (len(tickets) + 10 - 1) // 10

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = build_overdue_text(tickets, page=page, page_size=10)
    keyboard = overdue_tickets_keyboard(tickets, page=page, page_size=10)

    await _send_primary_ui(message_or_call, text, reply_markup=keyboard)
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

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

        await _send_primary_ui(
            message_or_call,
            text,
            reply_markup=main_menu_for_role(row_get(user, "role")),
        )
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer()
        return

    total_pages = (len(tickets) + 10 - 1) // 10

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    text = build_observer_tickets_text(tickets, title, page=page, page_size=10)
    keyboard = observer_tickets_keyboard(tickets, list_type=list_type, page=page, page_size=10)

    await _send_primary_ui(message_or_call, text, reply_markup=keyboard)
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

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

    await _send_primary_ui(message_or_call, text, reply_markup=observer_stats_menu_keyboard())
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()
