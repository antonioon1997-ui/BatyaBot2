from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import DEPARTMENT_PURCHASING, OPEN_STATUSES, department_by_role, normalize_department
from app.keyboards.tickets import active_search_prompt_keyboard, active_search_results_keyboard, ticket_notification_keyboard
from app.keyboards.productivity import (
    assignment_candidates_keyboard,
    day_off_keyboard,
    restore_day_off_keyboard,
    snooze_keyboard,
    ticket_summary_keyboard,
    transfer_request_keyboard,
    work_hub_keyboard,
)
from app.services.ticket_messages import send_live_ticket_text
from app.services.ui_context import get_ui_context, set_ticket_list_context, set_ui_context
from app.services.ui_messages import delete_trigger_message, is_primary_ui_message, send_ui_text
from app.services.ui_versions import pc_ticket_workspace_enabled
from app.services.tickets import get_ticket_by_id, get_active_users_by_department
from app.services.users import get_user_by_telegram_id
from app.services.work_management import (
    assign_ticket,
    clear_day_off,
    clear_ticket_snooze,
    count_unread_active_tickets,
    create_transfer_request,
    dismiss_day_off_restore,
    get_assigned_tickets,
    get_assignment_candidates,
    get_common_tickets,
    get_transfer_request,
    get_unread_active_tickets,
    mark_ticket_read,
    parse_moscow_datetime,
    process_transfer_request,
    quick_snooze_datetime,
    restore_day_off_tickets,
    search_active_tickets,
    set_day_off,
    set_ticket_summary,
    snooze_ticket,
)
from app.states import ProductivityStates
from app.utils import format_moscow_datetime, html_escape

from .utils import (
    can_user_view_ticket,
    get_current_user_and_admin,
    get_department_name,
    notify_department_about_ticket,
    row_get,
)
from .views import send_ticket_card, send_tickets_list
from .workspace import show_ticket_workspace, show_workspace_ticket

router = Router()
logger = logging.getLogger(__name__)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _is_executor(ticket, user) -> bool:
    return bool(ticket and user and department_by_role(row_get(user, "role")) == normalize_department(row_get(ticket, "executor_department")))


def _can_manage_summary(ticket, user, is_admin: bool) -> bool:
    return bool(is_admin or (_is_executor(ticket, user) and row_get(ticket, "status") in OPEN_STATUSES))


def _can_snooze(ticket, user, is_admin: bool) -> bool:
    return bool(
        ticket
        and row_get(ticket, "status") in OPEN_STATUSES
        and row_get(ticket, "executor_department") == DEPARTMENT_PURCHASING
        and (is_admin or department_by_role(row_get(user, "role")) == DEPARTMENT_PURCHASING)
    )


async def _is_workspace_call(call: CallbackQuery) -> bool:
    return bool(
        pc_ticket_workspace_enabled()
        and await is_primary_ui_message(call.from_user.id, getattr(call.message, "message_id", None))
    )


async def _return_workspace_list(target) -> None:
    context = await get_ui_context(target.from_user.id)
    list_type = context.list_type or "incoming"
    if list_type in {"active_search", "archive_search"}:
        # Поисковые результаты восстанавливают собственные handlers; после
        # отложения безопаснее вернуть пользователя в основной список.
        list_type = "incoming"
    await show_ticket_workspace(
        target,
        list_type=list_type,
        page=context.page,
        filters=context.filters_dict,
        mode="normal",
        answer_callback=False,
    )


async def _show_work_hub(target, user, is_admin: bool):
    department = department_by_role(row_get(user, "role")) if user else None
    count = await count_unread_active_tickets(target.from_user.id, department, is_admin=is_admin)
    text = (
        "📌 <b>Моя работа</b>\n\n"
        "Здесь собраны личные назначения, общие тикеты отдела, непрочитанные изменения, поиск и отметка выходных."
    )
    await send_ui_text(
        target.bot,
        chat_id=target.from_user.id,
        text=text,
        reply_markup=work_hub_keyboard(count),
    )
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.message(F.text == "📌 Моя работа")
async def bottom_work_hub(message: Message):
    user, is_admin = await get_current_user_and_admin(message.from_user.id)
    if not user:
        await message.answer("Нет доступа.")
        return
    await _show_work_hub(message, user, is_admin)


@router.callback_query(F.data == "work_hub")
async def callback_work_hub(call: CallbackQuery):
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    await _show_work_hub(call, user, is_admin)


@router.callback_query(F.data == "assigned_tickets")
async def callback_assigned_tickets(call: CallbackQuery):
    user, _ = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(call, list_type="assigned", page=0, filters={})
        return
    tickets = await get_assigned_tickets(call.from_user.id)
    await send_tickets_list(call, "👤 Назначенные мне", tickets)


@router.callback_query(F.data == "common_tickets")
async def callback_common_tickets(call: CallbackQuery):
    user, _ = await get_current_user_and_admin(call.from_user.id)
    department = department_by_role(row_get(user, "role")) if user else None
    if not user or not department:
        await call.answer("Раздел доступен сотрудникам отделов.", show_alert=True)
        return
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(call, list_type="common", page=0, filters={})
        return
    tickets = await get_common_tickets(call.from_user.id, department)
    await send_tickets_list(call, "📋 Общие тикеты отдела", tickets)


@router.callback_query(F.data == "unread_tickets")
async def callback_unread_tickets(call: CallbackQuery):
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    department = department_by_role(row_get(user, "role"))
    if pc_ticket_workspace_enabled():
        await show_ticket_workspace(call, list_type="unread", page=0, filters={})
        return
    tickets = await get_unread_active_tickets(call.from_user.id, department, is_admin=is_admin)
    await send_tickets_list(call, "🔔 Непрочитанные изменения", tickets)


@router.callback_query(F.data == "active_search")
async def callback_active_search(call: CallbackQuery, state: FSMContext):
    user, _ = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await state.set_state(ProductivityStates.waiting_active_search)
    text = "🔎 <b>Поиск активных тикетов</b>\n\nВведи номер тикета, номер заказа или слово из описания/комментариев."
    if pc_ticket_workspace_enabled():
        await set_ui_context(call.from_user.id, view="active_search_prompt", return_view="ticket_list")
        await send_ui_text(
            call.bot,
            chat_id=call.from_user.id,
            text=text,
            reply_markup=active_search_prompt_keyboard(),
        )
    else:
        await call.message.answer(text)
    await call.answer()


async def _show_active_search_results(target, query: str, *, page: int = 0, answer_callback: bool = True):
    user, is_admin = await get_current_user_and_admin(target.from_user.id)
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer("Нет доступа.", show_alert=True)
        else:
            await target.answer("Нет доступа.")
        return
    tickets = list(await search_active_tickets(
        query,
        target.from_user.id,
        department_by_role(row_get(user, "role")),
        is_admin=is_admin,
        limit=200,
    ))
    page_size = 5
    total = len(tickets)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = max(0, min(int(page), total_pages - 1))
    start = page * page_size
    page_tickets = tickets[start:start + page_size]
    await set_ticket_list_context(
        target.from_user.id,
        list_type="active_search",
        page=page,
        queue_ids=[int(row_get(ticket, "id")) for ticket in tickets],
        search_query=query,
        mode="normal",
        return_view="ticket_list",
    )
    if not tickets:
        text = f"🔎 По запросу «{html_escape(query)}» активных тикетов не найдено."
    else:
        lines = [
            "🔎 <b>Поиск активных тикетов</b>",
            f"Запрос: <code>{html_escape(query)}</code> · найдено {total}",
            "",
        ]
        for ticket in page_tickets:
            order = row_get(ticket, "order_number")
            order_part = f" · Заказ {html_escape(order)}" if order not in (None, "") else ""
            lines.append(
                f"<b>#{row_get(ticket, 'id')}</b>{order_part}\n"
                f"{html_escape(str(row_get(ticket, 'description') or '')[:110])}"
            )
        lines.extend(["", f"Показано {start + 1}–{min(start + page_size, total)} из {total}"])
        text = "\n\n".join(lines)
    await send_ui_text(
        target.bot,
        chat_id=target.from_user.id,
        text=text,
        reply_markup=active_search_results_keyboard(tickets, page=page, page_size=page_size),
    )
    if answer_callback and isinstance(target, CallbackQuery):
        await target.answer()


@router.message(ProductivityStates.waiting_active_search)
async def process_active_search(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Нужен текст для поиска.")
        return
    user, is_admin = await get_current_user_and_admin(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Нет доступа.")
        return
    query = message.text.strip()
    if pc_ticket_workspace_enabled():
        await _show_active_search_results(message, query, page=0)
        await state.clear()
        await delete_trigger_message(message)
        return
    tickets = await search_active_tickets(
        query,
        message.from_user.id,
        department_by_role(row_get(user, "role")),
        is_admin=is_admin,
    )
    await state.clear()
    await send_tickets_list(message, f"🔎 Поиск: {html_escape(query)}", tickets)


@router.callback_query(F.data.startswith("active_search_page:"))
async def callback_active_search_page(call: CallbackQuery):
    try:
        page = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("Некорректная страница.", show_alert=True)
        return
    context = await get_ui_context(call.from_user.id)
    query = str(context.search_query or "").strip()
    if not query:
        await call.answer("Поиск устарел. Начни новый поиск.", show_alert=True)
        return
    await _show_active_search_results(call, query, page=page)


@router.callback_query(F.data == "active_search_return")
async def callback_active_search_return(call: CallbackQuery):
    context = await get_ui_context(call.from_user.id)
    query = str(context.search_query or "").strip()
    if not query:
        await call.answer("Поиск устарел.", show_alert=True)
        return
    await _show_active_search_results(call, query, page=context.page)


@router.callback_query(F.data == "day_off_menu")
async def callback_day_off_menu(call: CallbackQuery):
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    if not user or not department_by_role(row_get(user, "role")):
        await call.answer("Раздел доступен сотрудникам отделов.", show_alert=True)
        return
    start = row_get(user, "day_off_start")
    end = row_get(user, "day_off_end")
    status = f"Текущий период: <b>{start} — {end}</b>" if start and end else "Выходные не установлены."
    await call.message.answer(
        "🏖 <b>Выходные</b>\n\n"
        f"{status}\n\n"
        "Если выходной начинается сегодня, назначенные тикеты сразу станут общими. "
        "При периоде с завтра это произойдёт автоматически в начале дня по МСК.",
        reply_markup=day_off_keyboard(bool(start and end)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("dayoff_set:"))
async def callback_set_day_off(call: CallbackQuery):
    _, offset_raw, duration_raw = call.data.split(":")
    user, _ = await get_current_user_and_admin(call.from_user.id)
    if not user or not department_by_role(row_get(user, "role")):
        await call.answer("Нет доступа.", show_alert=True)
        return
    start, end, released = await set_day_off(call.from_user.id, int(offset_raw), int(duration_raw), call.from_user.id)
    text = f"🏖 Выходные установлены: <b>{start} — {end}</b>."
    if released:
        text += f"\n\nВ общий список возвращено тикетов: <b>{len(released)}</b>."
    await call.message.answer(text)
    await call.answer("Сохранено.")


@router.callback_query(F.data == "dayoff_clear")
async def callback_clear_day_off(call: CallbackQuery):
    user, _ = await get_current_user_and_admin(call.from_user.id)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    candidates = await clear_day_off(call.from_user.id, call.from_user.id)
    if candidates:
        await call.message.answer(
            f"🟢 Выходной убран. Свободных тикетов, ранее снятых с тебя: <b>{len(candidates)}</b>.\n\n"
            "Можно вернуть их себе, но только если они всё ещё никем не заняты.",
            reply_markup=restore_day_off_keyboard(call.from_user.id),
        )
    else:
        await call.message.answer("🟢 Выходной убран. Свободных тикетов для возврата нет.")
    await call.answer()


@router.callback_query(F.data.startswith("dayoff_restore:"))
async def callback_restore_day_off(call: CallbackQuery):
    user_id = int(call.data.split(":")[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    if call.from_user.id != user_id and not is_admin:
        await call.answer("Вернуть тикеты может сотрудник или администратор.", show_alert=True)
        return
    restored = await restore_day_off_tickets(user_id, call.from_user.id)
    await call.message.answer(f"↩️ Возвращено тикетов: <b>{len(restored)}</b>.")
    await call.answer()


@router.callback_query(F.data.startswith("dayoff_restore_skip:"))
async def callback_skip_day_off_restore(call: CallbackQuery):
    user_id = int(call.data.split(":")[1])
    _, is_admin = await get_current_user_and_admin(call.from_user.id)
    if call.from_user.id != user_id and not is_admin:
        await call.answer("Нет доступа.", show_alert=True)
        return
    await dismiss_day_off_restore(user_id)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Тикеты оставлены общими.")


@router.callback_query(F.data.startswith("ticket_assign_self:"))
async def callback_assign_self(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, _ = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _is_executor(ticket, user) or row_get(ticket, "taken_by") is not None:
        await call.answer("Тикет уже назначен или недоступен.", show_alert=True)
        return
    changed = await assign_ticket(ticket_id, call.from_user.id, call.from_user.id, expected_assignee=None, reason="self_assignment")
    if not changed:
        await call.answer("Тикет уже успел взять другой сотрудник.", show_alert=True)
        return
    updated = await get_ticket_by_id(ticket_id)
    if await _is_workspace_call(call):
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
        await call.answer("Тикет назначен тебе.")
        return
    await call.message.answer(f"👤 Тикет #{ticket_id} назначен тебе.")
    await send_ticket_card(call, updated, user, False)


@router.callback_query(F.data.startswith("ticket_transfer_menu:"))
async def callback_transfer_menu(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, _ = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or int(row_get(ticket, "taken_by", 0) or 0) != call.from_user.id or not _is_executor(ticket, user):
        await call.answer("Передать тикет может текущий исполнитель.", show_alert=True)
        return
    users = await get_assignment_candidates(row_get(ticket, "executor_department"), exclude_user_id=call.from_user.id)
    text = f"🔄 <b>Передача тикета #{ticket_id}</b>\n\nВыбери нового исполнителя:"
    keyboard = assignment_candidates_keyboard(ticket_id, users, prefix="ticket_transfer_to", allow_common=True)
    if await _is_workspace_call(call):
        await send_ui_text(call.bot, chat_id=call.from_user.id, text=text, reply_markup=keyboard)
    else:
        await call.message.answer(text, reply_markup=keyboard)
    await call.answer()


@router.callback_query(F.data.startswith("ticket_transfer_to:"))
async def callback_transfer_to(call: CallbackQuery):
    _, ticket_raw, target_raw = call.data.split(":")
    ticket_id, target_id = int(ticket_raw), int(target_raw)
    user, _ = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or int(row_get(ticket, "taken_by", 0) or 0) != call.from_user.id or not _is_executor(ticket, user):
        await call.answer("Действие уже недоступно.", show_alert=True)
        return
    target = None if target_id == 0 else target_id
    changed = await assign_ticket(ticket_id, target, call.from_user.id, expected_assignee=call.from_user.id, reason="voluntary_transfer")
    if not changed:
        await call.answer("Передача не выполнена: состояние изменилось.", show_alert=True)
        return
    if target:
        try:
            updated_ticket = await get_ticket_by_id(ticket_id)
            target_user = await get_user_by_telegram_id(target)
            await send_live_ticket_text(
                call.bot,
                chat_id=target,
                ticket_id=ticket_id,
                text=f"👤 Тебе передан тикет #{ticket_id}.",
                reply_markup=ticket_notification_keyboard(updated_ticket, target_user),
            )
        except Exception:
            logger.exception("Не удалось уведомить нового исполнителя %s", target)
    if await _is_workspace_call(call):
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
        await call.answer("Исполнитель изменён.")
        return
    await call.message.answer(f"✅ Исполнитель тикета #{ticket_id} изменён.")
    await call.answer()


@router.callback_query(F.data.startswith("ticket_transfer_request:"))
async def callback_transfer_request(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, _ = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _is_executor(ticket, user) or not row_get(ticket, "taken_by") or int(row_get(ticket, "taken_by")) == call.from_user.id:
        await call.answer("Запрос передачи сейчас недоступен.", show_alert=True)
        return
    request_id = await create_transfer_request(ticket_id, call.from_user.id)
    if not request_id:
        await call.answer("Не удалось создать запрос.", show_alert=True)
        return
    assignee_id = int(row_get(ticket, "taken_by"))
    try:
        await send_live_ticket_text(
            call.bot,
            chat_id=assignee_id,
            ticket_id=ticket_id,
            text=f"🙋 Сотрудник {html_escape(row_get(user, 'full_name') or row_get(user, 'username') or call.from_user.id)} просит передать ему тикет #{ticket_id}.",
            reply_markup=transfer_request_keyboard(request_id, ticket_id),
        )
    except Exception:
        logger.exception("Не удалось отправить запрос передачи исполнителю %s", assignee_id)
        await call.answer("Исполнитель недоступен. Обратись к администратору.", show_alert=True)
        return
    await call.answer("Запрос отправлен текущему исполнителю.", show_alert=True)


@router.callback_query(F.data.startswith("transfer_approve:") | F.data.startswith("transfer_reject:"))
async def callback_process_transfer_request(call: CallbackQuery):
    approve = call.data.startswith("transfer_approve:")
    request_id = int(call.data.split(":")[1])
    ok, ticket_id, requester_id = await process_transfer_request(request_id, call.from_user.id, approve)
    if not ok:
        await call.answer("Запрос уже обработан или устарел.", show_alert=True)
        return
    if requester_id:
        try:
            updated_ticket = await get_ticket_by_id(ticket_id)
            requester_user = await get_user_by_telegram_id(requester_id)
            await send_live_ticket_text(
                call.bot,
                chat_id=requester_id,
                ticket_id=ticket_id,
                text=(f"✅ Тикет #{ticket_id} передан тебе." if approve else f"❌ Запрос на тикет #{ticket_id} отклонён."),
                reply_markup=ticket_notification_keyboard(updated_ticket, requester_user),
            )
        except Exception:
            logger.exception("Не удалось уведомить инициатора запроса %s", requester_id)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Тикет передан." if approve else "Запрос отклонён.")


@router.callback_query(F.data.startswith("admin_ticket_assign_menu:"))
async def callback_admin_assign_menu(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    if not is_admin:
        await call.answer("Только администратор.", show_alert=True)
        return
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket or row_get(ticket, "status") not in OPEN_STATUSES:
        await call.answer("Тикет недоступен.", show_alert=True)
        return
    users = await get_assignment_candidates(row_get(ticket, "executor_department"))
    text = f"👤 <b>Назначение тикета #{ticket_id}</b>\n\nВыбери исполнителя:"
    keyboard = assignment_candidates_keyboard(ticket_id, users, prefix="admin_ticket_assign_to", allow_common=True)
    if await _is_workspace_call(call):
        await send_ui_text(call.bot, chat_id=call.from_user.id, text=text, reply_markup=keyboard)
    else:
        await call.message.answer(text, reply_markup=keyboard)
    await call.answer()


@router.callback_query(F.data.startswith("admin_ticket_assign_to:"))
async def callback_admin_assign_to(call: CallbackQuery):
    _, ticket_raw, target_raw = call.data.split(":")
    _, is_admin = await get_current_user_and_admin(call.from_user.id)
    if not is_admin:
        await call.answer("Только администратор.", show_alert=True)
        return
    ticket_id, target_id = int(ticket_raw), int(target_raw)
    target = None if target_id == 0 else target_id
    changed = await assign_ticket(ticket_id, target, call.from_user.id, reason="admin_forced_assignment")
    if not changed:
        await call.answer("Назначение не выполнено.", show_alert=True)
        return
    if target:
        try:
            updated_ticket = await get_ticket_by_id(ticket_id)
            target_user = await get_user_by_telegram_id(target)
            await send_live_ticket_text(
                call.bot,
                chat_id=target,
                ticket_id=ticket_id,
                text=f"👤 Администратор назначил тебе тикет #{ticket_id}.",
                reply_markup=ticket_notification_keyboard(updated_ticket, target_user),
            )
        except Exception:
            logger.exception("Не удалось уведомить назначенного пользователя %s", target)
    if await _is_workspace_call(call):
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
        await call.answer("Назначение обновлено.")
        return
    await call.message.answer(f"✅ Назначение тикета #{ticket_id} обновлено.")
    await call.answer()


@router.callback_query(F.data.startswith("ticket_summary_menu:"))
async def callback_ticket_summary_menu(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_manage_summary(ticket, user, is_admin):
        await call.answer("Нет права изменять итог тикета.", show_alert=True)
        return
    current = html_escape(row_get(ticket, "current_summary"), default="—")
    next_action = html_escape(row_get(ticket, "next_action"), default="—")
    text = (
        f"📝 <b>Рабочая сводка тикета #{ticket_id}</b>\n\n"
        f"Текущий итог:\n{current}\n\nСледующее действие:\n{next_action}"
    )
    keyboard = ticket_summary_keyboard(ticket_id, bool(row_get(ticket, "current_summary") or row_get(ticket, "next_action")))
    if await _is_workspace_call(call):
        await send_ui_text(call.bot, chat_id=call.from_user.id, text=text, reply_markup=keyboard)
    else:
        await call.message.answer(text, reply_markup=keyboard)
    await call.answer()


@router.callback_query(F.data.startswith("ticket_summary_set:"))
async def callback_ticket_summary_set(call: CallbackQuery, state: FSMContext):
    _, ticket_raw, field = call.data.split(":")
    ticket_id = int(ticket_raw)
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_manage_summary(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    workspace_source = await _is_workspace_call(call)
    await state.clear()
    await state.update_data(ticket_id=ticket_id, entry_source="workspace" if workspace_source else "legacy")
    if field == "summary":
        await state.set_state(ProductivityStates.waiting_summary)
        prompt = "📝 <b>Текущий итог</b>\n\nНапиши краткий текущий итог тикета одним сообщением."
    else:
        await state.set_state(ProductivityStates.waiting_next_action)
        prompt = "➡️ <b>Следующее действие</b>\n\nНапиши следующее действие по тикету одним сообщением."
    if workspace_source:
        await send_ui_text(call.bot, chat_id=call.from_user.id, text=prompt)
    else:
        await call.message.answer(prompt)
    await call.answer()


@router.message(ProductivityStates.waiting_summary)
async def process_ticket_summary(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Нужен текст.")
        return
    data = await state.get_data()
    ticket_id = int(data["ticket_id"])
    user, is_admin = await get_current_user_and_admin(message.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_manage_summary(ticket, user, is_admin):
        await state.clear()
        await message.answer("Доступ изменился; итог не сохранён.")
        return
    workspace_source = str(data.get("entry_source") or "legacy") == "workspace" and pc_ticket_workspace_enabled()
    await set_ticket_summary(ticket_id, message.from_user.id, current_summary=message.text.strip()[:1500])
    await state.clear()
    if workspace_source:
        await delete_trigger_message(message)
        await show_workspace_ticket(message, ticket_id, answer_callback=False)
    else:
        await message.answer(f"✅ Краткий итог тикета #{ticket_id} обновлён.")


@router.message(ProductivityStates.waiting_next_action)
async def process_ticket_next_action(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Нужен текст.")
        return
    data = await state.get_data()
    ticket_id = int(data["ticket_id"])
    user, is_admin = await get_current_user_and_admin(message.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_manage_summary(ticket, user, is_admin):
        await state.clear()
        await message.answer("Доступ изменился; действие не сохранено.")
        return
    workspace_source = str(data.get("entry_source") or "legacy") == "workspace" and pc_ticket_workspace_enabled()
    await set_ticket_summary(ticket_id, message.from_user.id, next_action=message.text.strip()[:1500])
    await state.clear()
    if workspace_source:
        await delete_trigger_message(message)
        await show_workspace_ticket(message, ticket_id, answer_callback=False)
    else:
        await message.answer(f"✅ Следующее действие тикета #{ticket_id} обновлено.")


@router.callback_query(F.data.startswith("ticket_summary_clear:"))
async def callback_ticket_summary_clear(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_manage_summary(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await set_ticket_summary(ticket_id, call.from_user.id, clear=True)
    if await _is_workspace_call(call):
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
    await call.answer("Поля очищены.")


@router.callback_query(F.data.startswith("ticket_snooze_menu:"))
async def callback_ticket_snooze_menu(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_snooze(ticket, user, is_admin):
        await call.answer("Отложить тикет может отдел закупки или администратор.", show_alert=True)
        return
    text = f"⏰ <b>Отложить тикет #{ticket_id}</b>\n\nПока срок не наступит, он скрывается из рабочих списков закупки."
    keyboard = snooze_keyboard(ticket_id, bool(row_get(ticket, "snoozed_until")))
    if await _is_workspace_call(call):
        await send_ui_text(call.bot, chat_id=call.from_user.id, text=text, reply_markup=keyboard)
    else:
        await call.message.answer(text, reply_markup=keyboard)
    await call.answer()


@router.callback_query(F.data.startswith("ticket_snooze_quick:"))
async def callback_ticket_snooze_quick(call: CallbackQuery):
    _, ticket_raw, option = call.data.split(":")
    ticket_id = int(ticket_raw)
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_snooze(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    until = quick_snooze_datetime(option)
    changed = await snooze_ticket(ticket_id, call.from_user.id, until)
    if not changed:
        await call.answer("Не удалось отложить тикет.", show_alert=True)
        return
    if await _is_workspace_call(call):
        await _return_workspace_list(call)
        await call.answer(f"Тикет отложен до {until.strftime('%d.%m %H:%M')}.")
        return
    await call.message.answer(f"⏰ Тикет #{ticket_id} отложен до <b>{until.strftime('%d.%m.%Y %H:%M МСК')}</b>.")
    await call.answer()


@router.callback_query(F.data.startswith("ticket_snooze_custom:"))
async def callback_ticket_snooze_custom(call: CallbackQuery, state: FSMContext):
    ticket_id = int(call.data.split(":")[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_snooze(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    workspace_source = await _is_workspace_call(call)
    await state.clear()
    await state.set_state(ProductivityStates.waiting_snooze_datetime)
    await state.update_data(ticket_id=ticket_id, entry_source="workspace" if workspace_source else "legacy")
    prompt = "⏰ Введи дату и время по МСК, например: <code>30.07.2026 10:00</code> или <code>30.07 10:00</code>."
    if workspace_source:
        await send_ui_text(call.bot, chat_id=call.from_user.id, text=prompt)
    else:
        await call.message.answer(prompt)
    await call.answer()


@router.message(ProductivityStates.waiting_snooze_datetime)
async def process_ticket_snooze_custom(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Нужна дата и время текстом.")
        return
    until = parse_moscow_datetime(message.text)
    if until is None or until <= datetime.now(MOSCOW_TZ):
        await message.answer("Не удалось распознать будущее время. Пример: 30.07.2026 10:00")
        return
    data = await state.get_data()
    ticket_id = int(data["ticket_id"])
    user, is_admin = await get_current_user_and_admin(message.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_snooze(ticket, user, is_admin):
        await state.clear()
        await message.answer("Доступ изменился; тикет не отложен.")
        return
    workspace_source = str(data.get("entry_source") or "legacy") == "workspace" and pc_ticket_workspace_enabled()
    await snooze_ticket(ticket_id, message.from_user.id, until)
    await state.clear()
    if workspace_source:
        await delete_trigger_message(message)
        await _return_workspace_list(message)
    else:
        await message.answer(f"⏰ Тикет #{ticket_id} отложен до <b>{until.strftime('%d.%m.%Y %H:%M МСК')}</b>.")


@router.callback_query(F.data.startswith("ticket_snooze_clear:"))
async def callback_ticket_snooze_clear(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])
    user, is_admin = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    if not _can_snooze(ticket, user, is_admin):
        await call.answer("Нет доступа.", show_alert=True)
        return
    changed = await clear_ticket_snooze(ticket_id, call.from_user.id)
    await call.answer("Тикет возвращён в рабочие списки." if changed else "Тикет уже не отложен.", show_alert=not changed)
