from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.tickets import archive_search_results_keyboard
from app.services.tickets import search_archive_tickets
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
        "🔎 <b>Поиск по архиву</b>\n\n"
        "Отправь слово, фразу или номер заказа.\n\n"
        "Поиск выполняется только по завершённым и отменённым тикетам:\n"
        "• по номеру заказа;\n"
        "• по описанию тикета;\n"
        "• по комментариям.\n\n"
        "Например: <code>12345</code> или <code>не пришёл товар</code>."
    )

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

    await state.update_data(
        archive_search_query=query,
        archive_search_ticket_ids=[int(row_get(ticket, "id")) for ticket in tickets],
    )

    if not tickets:
        text = (
            f"🔎 По запросу «{html_escape(query)}» ничего не найдено.\n\n"
            "Проверь написание или попробуй более короткое слово/номер заказа."
        )

        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(text, reply_markup=archive_search_results_keyboard([]))
            await message_or_call.answer()
        else:
            await message_or_call.answer(text, reply_markup=archive_search_results_keyboard([]))
        return

    page_size = 10
    total = len(tickets)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = start + page_size
    page_tickets = tickets[start:end]

    lines = [
        f"🔎 <b>Результаты поиска по архиву</b>",
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

    text = "\n\n".join(lines)
    keyboard = archive_search_results_keyboard(
        tickets,
        page=page,
        page_size=page_size,
    )

    if isinstance(message_or_call, CallbackQuery):
        await answer_long(message_or_call, text, reply_markup=keyboard)
        await message_or_call.answer()
    else:
        await answer_long(message_or_call, text, reply_markup=keyboard)

@router.message(F.text == "🔎 Поиск по архиву")
async def bottom_archive_search(message: Message, state: FSMContext):
    await start_archive_search(message, state)

@router.callback_query(F.data == "archive_search")
async def callback_archive_search(call: CallbackQuery, state: FSMContext):
    await start_archive_search(call, state)

@router.message(TicketActionStates.waiting_archive_search_query)
async def process_archive_search_query(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("Отправь текст, слово или номер заказа для поиска.")
        return

    query = message.text.strip()

    if len(query) < 2:
        await message.answer("Запрос слишком короткий. Введи минимум 2 символа.")
        return

    await send_archive_search_results(
        message_or_call=message,
        state=state,
        query=query,
        page=0,
    )

@router.callback_query(F.data.startswith("archive_search_page:"))
async def callback_archive_search_page(call: CallbackQuery, state: FSMContext):
    try:
        page = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await call.answer("Некорректная страница.", show_alert=True)
        return

    data = await state.get_data()
    query = str(data.get("archive_search_query") or "").strip()

    if not query:
        await call.answer("Поиск устарел. Начни новый поиск.", show_alert=True)
        await start_archive_search(call, state)
        return

    await send_archive_search_results(
        message_or_call=call,
        state=state,
        query=query,
        page=page,
    )
