from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.keyboards.tickets import (
    ticket_workspace_keyboard,
    workspace_history_keyboard,
    workspace_ticket_action_keyboard,
    workspace_ticket_more_keyboard,
)
from app.services.attachments import get_ticket_attachments
from app.services.order_status import OrderStatusUnavailable, get_order_status
from app.services.ticket_messages import delete_message_ids
from app.services.tickets import (
    get_archive_incoming_tickets,
    get_archive_outgoing_tickets,
    get_filtered_tickets,
    get_incoming_tickets,
    get_outgoing_tickets,
    get_ticket_by_id,
    get_ticket_comments,
    get_ticket_events,
    get_work_tickets,
    update_ticket_status,
)
from app.services.ui_context import get_ui_context, set_ticket_context, set_ticket_list_context, set_ui_context
from app.services.ui_messages import clear_ui_message_bundle, replace_ui_message_bundle, send_ui_text
from app.services.work_management import (
    get_assigned_tickets,
    get_common_tickets,
    get_unread_active_tickets,
    mark_ticket_read,
)
from app.utils import format_moscow_datetime, html_escape

from .utils import (
    can_user_resolve_ticket,
    can_user_view_ticket,
    department_by_role,
    get_author_name_from_ticket,
    get_current_user_and_admin,
    get_status_name,
    has_text_value,
    is_client_to_purchasing_ticket,
    row_get,
    short_text,
)
from .views import send_completed_ticket_card_to_creator

router = Router()
logger = logging.getLogger(__name__)

WORKSPACE_PAGE_SIZE = 5
ATTACHMENTS_SLOT = "ticket_attachments"


def _list_title(list_type: str) -> str:
    return {
        "incoming": "📥 Входящие",
        "work": "🛠 В работе",
        "outgoing": "📤 Исходящие",
        "archive": "📦 Архив",
        "assigned": "👤 Назначенные мне",
        "common": "📋 Общие",
        "unread": "🔔 Непрочитанные",
        "active_search": "🔎 Поиск",
        "archive_search": "🔎 Архив / поиск",
    }.get(list_type, "📂 Тикеты")


def _filter_label(filters: dict) -> str | None:
    if not filters:
        return None
    labels = []
    if filters.get("status"):
        labels.append(f"статус: {filters['status']}")
    if filters.get("priority"):
        labels.append(f"приоритет: {filters['priority']}")
    if filters.get("category"):
        labels.append(f"тип: {filters['category']}")
    if filters.get("has_attachments") is True:
        labels.append("с вложениями")
    elif filters.get("has_attachments") is False:
        labels.append("без вложений")
    if filters.get("date_days"):
        labels.append(f"за {filters['date_days']} дн.")
    if filters.get("overdue_only"):
        labels.append("просроченные")
    if filters.get("filter_department"):
        labels.append(f"отдел: {filters['filter_department']}")
    return ", ".join(labels) if labels else None


async def _load_tickets(user_id: int, user, is_admin: bool, list_type: str, filters: dict | None = None):
    department = None if is_admin else department_by_role(row_get(user, "role"))
    filters = dict(filters or {})
    if filters:
        return list(
            await get_filtered_tickets(
                user_id,
                department,
                list_type,
                limit=250,
                **filters,
            )
        )

    if list_type == "incoming":
        return list(await get_incoming_tickets(department=department, limit=None))
    if list_type == "outgoing":
        return list(await get_outgoing_tickets(user_id, limit=250))
    if list_type == "work":
        if is_admin:
            return list(await get_work_tickets(limit=250))
        return list(await get_work_tickets(telegram_id=user_id, department=department, limit=250))
    if list_type == "archive":
        # Универсальный архив workspace: все доступные пользователю закрытые тикеты.
        return list(await get_filtered_tickets(user_id, department, "archive", limit=250))
    if list_type == "assigned":
        return list(await get_assigned_tickets(user_id, limit=250))
    if list_type == "common":
        if not department:
            return []
        return list(await get_common_tickets(user_id, department, limit=250))
    if list_type == "unread":
        return list(await get_unread_active_tickets(user_id, department, is_admin=is_admin, limit=250))
    return []


async def show_ticket_workspace(
    target,
    *,
    list_type: str | None = None,
    page: int | None = None,
    filters: dict | None = None,
    mode: str | None = None,
    answer_callback: bool = True,
) -> None:
    user, is_admin = await get_current_user_and_admin(target.from_user.id)
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer("Нет доступа.", show_alert=True)
        else:
            await target.answer("Нет доступа.")
        return

    context = await get_ui_context(target.from_user.id)
    selected_type = list_type or context.list_type or "incoming"
    selected_page = context.page if page is None and context.list_type == selected_type else int(page or 0)
    selected_filters = dict(filters if filters is not None else (context.filters_dict if context.list_type == selected_type else {}))
    selected_mode = mode or (context.mode if context.list_type == selected_type else "normal")
    if selected_mode not in {"normal", "review"}:
        selected_mode = "normal"

    tickets = await _load_tickets(target.from_user.id, user, is_admin, selected_type, selected_filters)
    total = len(tickets)
    total_pages = max((total + WORKSPACE_PAGE_SIZE - 1) // WORKSPACE_PAGE_SIZE, 1)
    selected_page = max(0, min(selected_page, total_pages - 1))
    start = selected_page * WORKSPACE_PAGE_SIZE
    page_tickets = tickets[start : start + WORKSPACE_PAGE_SIZE]

    filter_text = _filter_label(selected_filters)
    lines = ["📂 <b>Работа с тикетами</b>", f"{_list_title(selected_type)} · найдено {total}"]
    if filter_text:
        lines.append(f"🔽 Фильтр: {html_escape(filter_text)}")
    if selected_mode == "review":
        lines.append("🚀 Режим разбора активен")
    lines.append("")

    if not page_tickets:
        lines.append("Здесь пока нет тикетов.")
    else:
        for ticket in page_tickets:
            order = row_get(ticket, "order_number")
            order_part = f" · Заказ {html_escape(order)}" if has_text_value(order) else ""
            attachment_count = 0
            try:
                attachment_count = len(await get_ticket_attachments(int(ticket["id"])))
            except Exception:
                logger.debug("Не удалось получить вложения тикета %s для preview", ticket["id"], exc_info=True)
            indicators = " 📎" if attachment_count else ""
            unread = " 🔵" if row_get(ticket, "has_unread", 0) else ""
            lines.append(
                f"<b>#{ticket['id']}</b>{order_part} · {get_status_name(row_get(ticket, 'status'))}{indicators}{unread}\n"
                f"{short_text(row_get(ticket, 'description'), 110)}"
            )
    if total:
        end = min(start + WORKSPACE_PAGE_SIZE, total)
        lines.extend(["", f"Показано {start + 1}–{end} из {total}"])

    queue_ids = [int(row_get(ticket, "id")) for ticket in tickets]
    await set_ticket_list_context(
        target.from_user.id,
        list_type=selected_type,
        page=selected_page,
        queue_ids=queue_ids,
        filters=selected_filters,
        mode=selected_mode,
    )
    await clear_ui_message_bundle(target.bot, chat_id=target.from_user.id, slot=ATTACHMENTS_SLOT)
    await send_ui_text(
        target.bot,
        chat_id=target.from_user.id,
        text="\n\n".join(lines),
        reply_markup=ticket_workspace_keyboard(
            page_tickets,
            list_type=selected_type,
            page=selected_page,
            page_size=WORKSPACE_PAGE_SIZE,
            total=total,
            review_mode=(selected_mode == "review"),
        ),
    )
    if answer_callback and isinstance(target, CallbackQuery):
        await target.answer()


def _back_callback_for_context(context) -> str:
    if context.list_type == "archive_search":
        return "archive_search_return"
    if context.list_type == "active_search":
        return "active_search_return"
    return "workspace_back_to_list"


async def _current_order_block(ticket, user, is_admin: bool) -> str | None:
    """Актуальный блок OrderExporter для workspace-карточки.

    Для закупки/админа возвращаем полный рабочий набор: статус МС и активные
    назначения заказов поставщиков. Для клиентского отдела — статус МС и товары
    без внутренних номеров заказов поставщиков.
    """
    order_number = row_get(ticket, "order_number")
    if not has_text_value(order_number):
        return None

    try:
        lookup = await get_order_status(str(order_number))
    except (OrderStatusUnavailable, ValueError):
        return None
    if not lookup.record:
        return None

    record = lookup.record
    user_department = department_by_role(row_get(user, "role"))
    purchasing_view = bool(is_admin or user_department == "purchasing")
    heading = "Статусы заказов поставщиков:" if purchasing_view else "Товары в заказе:"
    items = record.purchasing_items if purchasing_view else record.client_items

    lines = [
        "📦 <b>Заказ сейчас</b>",
        f"Статус МС: {html_escape(record.ms_status)}",
        "",
        f"<b>{heading}</b>",
        *(html_escape(item) for item in items),
    ]
    if lookup.stale:
        lines.extend(["", "⚠️ <i>Данные OrderExporter давно не обновлялись.</i>"])
    return "\n".join(lines)


async def build_workspace_ticket_text(ticket, user, is_admin: bool, *, position: int | None, total: int | None, mode: str) -> str:
    prefix = "🚀 Разбор · " if mode == "review" else ""
    pos = f" · {position + 1} из {total}" if position is not None and total else ""
    lines = [f"{prefix}🎫 <b>Тикет #{ticket['id']}</b>{pos}", ""]
    order = row_get(ticket, "order_number")
    if has_text_value(order):
        lines.append(f"🔢 Заказ: {html_escape(order)}")
    lines.append(f"👤 Автор: {get_author_name_from_ticket(ticket)}")
    lines.append(f"📌 Статус: {get_status_name(row_get(ticket, 'status'))}")
    assignee = row_get(ticket, "assignee_full_name") or row_get(ticket, "assignee_username") or row_get(ticket, "taken_by")
    lines.append(f"👤 Исполнитель: {html_escape(assignee)}" if assignee else "👥 Исполнитель: общий тикет отдела")
    lines.append(f"🕒 Создан: {format_moscow_datetime(row_get(ticket, 'created_at'))}")
    lines.extend(["", "📝 <b>Описание</b>", short_text(row_get(ticket, "description"), 850)])

    comments = await get_ticket_comments(int(ticket["id"]), limit=None)
    if comments:
        latest = comments[-1]
        author = row_get(latest, "author_name") or row_get(latest, "author_username") or row_get(latest, "author_telegram_id")
        lines.extend([
            "",
            "💬 <b>Последний комментарий</b>",
            f"{html_escape(author)}: {short_text(row_get(latest, 'text'), 350)}",
        ])

    order_block = await _current_order_block(ticket, user, is_admin)
    if order_block:
        lines.extend(["", order_block])
    if has_text_value(row_get(ticket, "current_summary")):
        lines.extend(["", "📍 <b>Текущий итог:</b>", short_text(row_get(ticket, "current_summary"), 450)])
    if has_text_value(row_get(ticket, "next_action")):
        lines.extend(["", "➡️ <b>Следующее действие:</b>", short_text(row_get(ticket, "next_action"), 450)])
    attachments = await get_ticket_attachments(int(ticket["id"]))
    if attachments:
        lines.extend(["", f"📎 Вложения: {len(attachments)}"])
    return "\n".join(lines)


async def show_workspace_ticket(
    target,
    ticket_id: int,
    *,
    position: int | None = None,
    answer_callback: bool = True,
) -> None:
    user, is_admin = await get_current_user_and_admin(target.from_user.id)
    ticket = await get_ticket_by_id(int(ticket_id))
    if not ticket or not can_user_view_ticket(ticket, user, is_admin):
        if isinstance(target, CallbackQuery):
            await target.answer("Тикет не найден или нет доступа.", show_alert=True)
        else:
            await target.answer("Тикет не найден или нет доступа.")
        return

    context = await get_ui_context(target.from_user.id)
    queue = context.queue
    if int(ticket_id) not in queue:
        queue = [int(ticket_id)]
        await set_ui_context(target.from_user.id, queue_ids=queue, list_type=context.list_type or "incoming", page=context.page)
    if position is None:
        try:
            position = queue.index(int(ticket_id))
        except ValueError:
            position = 0
    mode = context.mode if context.mode in {"normal", "review"} else "normal"
    await set_ticket_context(target.from_user.id, ticket_id=int(ticket_id), current_index=position, mode=mode)
    await clear_ui_message_bundle(target.bot, chat_id=target.from_user.id, slot=ATTACHMENTS_SLOT)
    try:
        await mark_ticket_read(int(ticket_id), target.from_user.id)
    except Exception:
        logger.debug("Не удалось отметить тикет %s прочитанным", ticket_id, exc_info=True)

    text = await build_workspace_ticket_text(ticket, user, is_admin, position=position, total=len(queue), mode=mode)
    await send_ui_text(
        target.bot,
        chat_id=target.from_user.id,
        text=text,
        reply_markup=workspace_ticket_action_keyboard(
            ticket,
            user,
            is_admin,
            position=position,
            total=len(queue),
            review_mode=(mode == "review"),
            back_callback=_back_callback_for_context(context),
        ),
    )
    if answer_callback and isinstance(target, CallbackQuery):
        await target.answer()


async def _show_neighbor(call: CallbackQuery, offset: int) -> None:
    context = await get_ui_context(call.from_user.id)
    queue = context.queue
    if not queue:
        await call.answer("Список устарел. Возвращаю к тикетам.", show_alert=False)
        await show_ticket_workspace(
            call,
            list_type=context.list_type or "incoming",
            page=context.page,
            answer_callback=False,
        )
        return
    current = context.current_index
    if current is None and context.current_ticket_id in queue:
        current = queue.index(context.current_ticket_id)
    current = int(current or 0)
    index = current + offset
    while 0 <= index < len(queue):
        ticket = await get_ticket_by_id(queue[index])
        user, is_admin = await get_current_user_and_admin(call.from_user.id)
        if ticket and can_user_view_ticket(ticket, user, is_admin):
            await show_workspace_ticket(call, int(ticket["id"]), position=index)
            return
        index += offset
    await call.answer("Больше доступных тикетов в этом направлении нет.")


@router.callback_query(F.data.startswith("workspace_list:"))
async def callback_workspace_list(call: CallbackQuery):
    _, list_type, page_raw = call.data.split(":", 2)
    try:
        page = int(page_raw)
    except ValueError:
        page = 0
    context = await get_ui_context(call.from_user.id)
    filters = context.filters_dict if context.list_type == list_type else {}
    await show_ticket_workspace(call, list_type=list_type, page=page, filters=filters, mode="normal")


@router.callback_query(F.data.startswith("workspace_ticket:"))
async def callback_workspace_ticket(call: CallbackQuery):
    await show_workspace_ticket(call, int(call.data.split(":", 1)[1]))


@router.callback_query(F.data == "workspace_back_to_list")
async def callback_workspace_back(call: CallbackQuery):
    context = await get_ui_context(call.from_user.id)
    await show_ticket_workspace(
        call,
        list_type=context.list_type or "incoming",
        page=context.page,
        filters=context.filters_dict,
        mode="normal" if context.mode != "review" else "review",
    )


@router.callback_query(F.data == "workspace_prev_ticket")
async def callback_workspace_prev(call: CallbackQuery):
    await _show_neighbor(call, -1)


@router.callback_query(F.data == "workspace_next_ticket")
async def callback_workspace_next(call: CallbackQuery):
    await _show_neighbor(call, 1)


@router.callback_query(F.data.startswith("workspace_more:"))
async def callback_workspace_more(call: CallbackQuery):
    ticket_id = int(call.data.split(":", 1)[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or not can_user_view_ticket(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    context = await get_ui_context(call.from_user.id)
    text = await build_workspace_ticket_text(
        ticket,
        user,
        is_admin,
        position=context.current_index,
        total=len(context.queue),
        mode=context.mode,
    )
    await send_ui_text(
        call.bot,
        chat_id=call.from_user.id,
        text=text,
        reply_markup=workspace_ticket_more_keyboard(ticket, user, is_admin),
    )
    await call.answer()


@router.callback_query(F.data.startswith("workspace_history:"))
async def callback_workspace_history(call: CallbackQuery):
    ticket_id = int(call.data.split(":", 1)[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or not can_user_view_ticket(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    comments = await get_ticket_comments(ticket_id, limit=None)
    events = await get_ticket_events(ticket_id, limit=100)
    lines = [f"📜 <b>Полная история тикета #{ticket_id}</b>", "", "📝 Описание:", html_escape(row_get(ticket, "description"))]
    if comments:
        lines.extend(["", "💬 <b>Комментарии</b>"])
        for comment in comments:
            author = row_get(comment, "author_name") or row_get(comment, "author_username") or row_get(comment, "author_telegram_id")
            lines.append(f"— {html_escape(author)} [{format_moscow_datetime(row_get(comment, 'created_at'))}]:\n{html_escape(row_get(comment, 'text'))}")
    if events:
        lines.extend(["", "🕓 <b>История действий</b>"])
        for event in events:
            actor = row_get(event, "actor_name") or row_get(event, "actor_username") or row_get(event, "actor_telegram_id") or "Система"
            details = f" — {html_escape(row_get(event, 'details'))}" if has_text_value(row_get(event, "details")) else ""
            lines.append(f"— {format_moscow_datetime(row_get(event, 'created_at'))}: {html_escape(row_get(event, 'event_type'))} ({html_escape(actor)}){details}")
    context = await get_ui_context(call.from_user.id)
    await send_ui_text(
        call.bot,
        chat_id=call.from_user.id,
        text="\n\n".join(lines),
        reply_markup=workspace_history_keyboard(
            ticket_id,
            back_callback=_back_callback_for_context(context),
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("workspace_attachments:"))
async def callback_workspace_attachments(call: CallbackQuery):
    ticket_id = int(call.data.split(":", 1)[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or not can_user_view_ticket(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    attachments = await get_ticket_attachments(ticket_id)
    if not attachments:
        await call.answer("У тикета нет вложений.")
        return
    await clear_ui_message_bundle(call.bot, chat_id=call.from_user.id, slot=ATTACHMENTS_SLOT)
    sent_ids: list[int] = []
    try:
        for index, attachment in enumerate(attachments, start=1):
            file_id = row_get(attachment, "file_id")
            file_type = row_get(attachment, "file_type")
            caption = f"📎 Тикет #{ticket_id} · вложение {index}/{len(attachments)}"
            if file_type == "photo":
                sent = await call.bot.send_photo(chat_id=call.from_user.id, photo=file_id, caption=caption)
            elif file_type == "document":
                sent = await call.bot.send_document(chat_id=call.from_user.id, document=file_id, caption=caption)
            elif file_type == "video":
                sent = await call.bot.send_video(chat_id=call.from_user.id, video=file_id, caption=caption)
            else:
                continue
            sent_ids.append(int(sent.message_id))
        await replace_ui_message_bundle(call.bot, chat_id=call.from_user.id, new_message_ids=sent_ids, slot=ATTACHMENTS_SLOT)
    except Exception:
        await delete_message_ids(call.bot, call.from_user.id, sent_ids)
        logger.exception("Не удалось показать вложения тикета %s", ticket_id)
        await call.answer("Не удалось показать вложения.", show_alert=True)
        return
    await call.answer(f"Показано вложений: {len(sent_ids)}")


@router.callback_query(F.data.startswith("workspace_review_start:"))
async def callback_workspace_review_start(call: CallbackQuery):
    list_type = call.data.split(":", 1)[1]
    context = await get_ui_context(call.from_user.id)
    await show_ticket_workspace(
        call,
        list_type=list_type,
        page=context.page if context.list_type == list_type else 0,
        filters=context.filters_dict if context.list_type == list_type else {},
        mode="review",
        answer_callback=False,
    )
    context = await get_ui_context(call.from_user.id)
    if context.queue:
        await show_workspace_ticket(call, context.queue[0], position=0)
    else:
        await call.answer("В этом списке нет тикетов для разбора.")


@router.callback_query(F.data == "workspace_review_stop")
async def callback_workspace_review_stop(call: CallbackQuery):
    context = await get_ui_context(call.from_user.id)
    await set_ui_context(call.from_user.id, mode="normal")
    await show_ticket_workspace(call, list_type=context.list_type or "incoming", page=context.page, filters=context.filters_dict, mode="normal")


@router.callback_query(F.data.startswith("workspace_review_resolve:"))
async def callback_workspace_review_resolve(call: CallbackQuery):
    ticket_id = int(call.data.split(":", 1)[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or not can_user_resolve_ticket(ticket, user):
        await call.answer("Ты не можешь выполнить этот тикет.", show_alert=True)
        return
    target_status = "done" if is_client_to_purchasing_ticket(ticket) else "waiting_confirmation"
    changed = await update_ticket_status(
        ticket_id,
        target_status,
        actor_telegram_id=call.from_user.id,
        comment=None,
        expected_statuses=("new", "in_work"),
    )
    if not changed:
        await call.answer("Состояние тикета уже изменилось.", show_alert=True)
        return
    updated = await get_ticket_by_id(ticket_id)
    await send_completed_ticket_card_to_creator(call.bot, updated, headline="")
    context = await get_ui_context(call.from_user.id)
    queue = context.queue
    current = context.current_index if context.current_index is not None else 0
    next_index = int(current) + 1
    while next_index < len(queue):
        candidate = await get_ticket_by_id(queue[next_index])
        if candidate and can_user_view_ticket(candidate, user, is_admin):
            await show_workspace_ticket(call, int(candidate["id"]), position=next_index)
            return
        next_index += 1
    await set_ui_context(call.from_user.id, mode="normal")
    await show_ticket_workspace(
        call,
        list_type=context.list_type or "incoming",
        page=context.page,
        filters=context.filters_dict,
        mode="normal",
    )
