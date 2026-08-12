from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.tickets import archive_search_prompt_keyboard, archive_search_results_keyboard
from app.services.tickets import search_archive_tickets
from app.services.ui_context import get_ui_context, set_ticket_list_context, set_ui_context
from app.services.ui_messages import delete_trigger_message, send_ui_text
from app.services.ui_versions import pc_ticket_workspace_enabled
from app.states import TicketActionStates
from app.utils import html_escape

from .utils import (
    answer_long,
    department_by_role,
    get_author_name_from_ticket,
    get_current_user_and_admin,
    get_status_name,
    is_observer_role,
    optional_line,
    row_get,
    short_text,
)

router = Router()
ARCHIVE_SEARCH_PAGE_SIZE = 5


async def start_archive_search(message_or_call, state: FSMContext):
    user, admin_flag = await get_current_user_and_admin(message_or_call.from_user.id)

    if not user:
        text = "У тебя пока нет доступа к боту. Отправь /start и дождись одобрения."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(text, show_alert=True)
        else:
            await message_or_call.answer(text)
        return

    await state.clear()
    await state.set_state(TicketActionStates.waiting_archive_search_query)

    text = (
        "🔎 <b>Архив / поиск</b>\n\n"
        "Введи номер заказа, номер тикета или ключевое слово.\n\n"
        "Ищем по завершённым и отменённым тикетам: номер тикета, заказ, описание и комментарии."
    )

    if pc_ticket_workspace_enabled() and not is_observer_role(row_get(user, "role")):
        await set_ui_context(
            message_or_call.from_user.id,
            view="archive_search_prompt",
            return_view="archive",
            mode="normal",
        )
        await send_ui_text(
            message_or_call.bot,
            chat_id=message_or_call.from_user.id,
            text=text,
            reply_markup=archive_search_prompt_keyboard(),
        )
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer()
        return

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.answer(text)
        await message_or_call.answer()
    else:
        await message_or_call.answer(text)


async def send_archive_search_results(
    message_or_call,
    state: FSMContext,
    query: str,
    page: int = 0,
    *,
    answer_callback: bool = True,
):
    user, admin_flag = await get_current_user_and_admin(message_or_call.from_user.id)

    if not user:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Нет доступа.", show_alert=True)
        else:
            await message_or_call.answer("Нет доступа.")
        return

    role = row_get(user, "role")
    observer_flag = is_observer_role(role)
    department = department_by_role(role)

    tickets = await search_archive_tickets(
        query=query,
        telegram_id=message_or_call.from_user.id,
        department=department,
        is_observer=observer_flag,
        is_admin=admin_flag,
        limit=200,
    )

    queue_ids = [int(row_get(ticket, "id")) for ticket in tickets]
    await state.update_data(
        archive_search_query=query,
        archive_search_ticket_ids=queue_ids,
    )

    if pc_ticket_workspace_enabled() and not observer_flag:
        total = len(tickets)
        total_pages = max((total + ARCHIVE_SEARCH_PAGE_SIZE - 1) // ARCHIVE_SEARCH_PAGE_SIZE, 1)
        page = max(0, min(int(page), total_pages - 1))
        start = page * ARCHIVE_SEARCH_PAGE_SIZE
        page_tickets = tickets[start : start + ARCHIVE_SEARCH_PAGE_SIZE]

        await set_ticket_list_context(
            message_or_call.from_user.id,
            list_type="archive_search",
            page=page,
            queue_ids=queue_ids,
            filters={},
            search_query=query,
            mode="normal",
            return_view="archive",
        )

        if not tickets:
            text = (
                f"🔎 По запросу «{html_escape(query)}» ничего не найдено.\n\n"
                "Проверь написание или попробуй более короткий запрос."
            )
        else:
            lines = [
                "🔎 <b>Архив / результаты поиска</b>",
                f"Запрос: <code>{html_escape(query)}</code> · найдено {total}",
                "",
            ]
            for ticket in page_tickets:
                order = row_get(ticket, "order_number")
                order_part = f" · Заказ {html_escape(order)}" if order not in (None, "") else ""
                lines.append(
                    f"<b>#{row_get(ticket, 'id')}</b>{order_part} · {get_status_name(row_get(ticket, 'status'))}\n"
                    f"{short_text(row_get(ticket, 'description'), 110)}"
                )
            lines.extend(["", f"Показано {start + 1}–{min(start + ARCHIVE_SEARCH_PAGE_SIZE, total)} из {total}"])
            text = "\n\n".join(lines)

        await send_ui_text(
            message_or_call.bot,
            chat_id=message_or_call.from_user.id,
            text=text,
            reply_markup=archive_search_results_keyboard(
                tickets,
                page=page,
                page_size=ARCHIVE_SEARCH_PAGE_SIZE,
            ),
        )
        if answer_callback and isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer()
        return

    # Legacy / observer renderer kept for UI rollback compatibility.
    if not tickets:
        text = (
            f"🔎 По запросу «{html_escape(query)}» ничего не найдено.\n\n"
            "Проверь написание или попробуй более короткое слово/номер заказа."
        )
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(text, reply_markup=archive_search_results_keyboard([]))
            if answer_callback:
                await message_or_call.answer()
        else:
            await message_or_call.answer(text, reply_markup=archive_search_results_keyboard([]))
        return

    page_size = 10
    total = len(tickets)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    page_tickets = tickets[start : start + page_size]

    lines = [
        "🔎 <b>Результаты поиска по архиву</b>",
        f"Запрос: <code>{html_escape(query)}</code>",
        f"Найдено: {total}",
        f"Страница: {page + 1}/{total_pages}",
        "",
    ]
    for ticket in page_tickets:
        order_line = optional_line("🔢 Заказ: ", row_get(ticket, "order_number"))
        lines.append(
            f"<b>#{row_get(ticket, 'id')}</b> — {get_status_name(row_get(ticket, 'status'))}\n"
            f"{order_line}"
            f"👤 Автор: {get_author_name_from_ticket(ticket)}\n"
            f"📝 {short_text(row_get(ticket, 'description'), 500)}"
        )
    await answer_long(
        message_or_call,
        "\n\n".join(lines),
        reply_markup=archive_search_results_keyboard(tickets, page=page, page_size=page_size),
    )
    if answer_callback and isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()


@router.message(F.text == "🔎 Поиск по архиву")
async def bottom_archive_search(message: Message, state: FSMContext):
    await start_archive_search(message, state)


@router.callback_query(F.data == "archive_search")
async def callback_archive_search(call: CallbackQuery, state: FSMContext):
    await start_archive_search(call, state)


@router.message(TicketActionStates.waiting_archive_search_query)
async def process_archive_search_query(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Отправь текст, номер тикета или номер заказа для поиска.")
        return

    query = message.text.strip()
    if len(query) < 2:
        await message.answer("Запрос слишком короткий. Введи минимум 2 символа.")
        return

    await send_archive_search_results(message, state, query=query, page=0)
    if pc_ticket_workspace_enabled():
        await state.clear()
        await delete_trigger_message(message)


@router.callback_query(F.data.startswith("archive_search_page:"))
async def callback_archive_search_page(call: CallbackQuery, state: FSMContext):
    try:
        page = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("Некорректная страница.", show_alert=True)
        return

    context = await get_ui_context(call.from_user.id)
    query = str(context.search_query or "").strip() if pc_ticket_workspace_enabled() else ""
    if not query:
        data = await state.get_data()
        query = str(data.get("archive_search_query") or "").strip()

    if not query:
        await call.answer("Поиск устарел. Начни новый поиск.", show_alert=True)
        await start_archive_search(call, state)
        return

    await send_archive_search_results(call, state, query=query, page=page)


@router.callback_query(F.data == "archive_search_return")
async def callback_archive_search_return(call: CallbackQuery, state: FSMContext):
    context = await get_ui_context(call.from_user.id)
    query = str(context.search_query or "").strip()
    if not query:
        await start_archive_search(call, state)
        return
    await send_archive_search_results(
        call,
        state,
        query=query,
        page=context.page,
    )
