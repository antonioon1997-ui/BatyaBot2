from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.keyboards.observer import (
    observer_stats_menu_keyboard,
    observer_stats_period_keyboard,
    observer_users_keyboard,
)
from app.services.tickets import get_observer_report, get_users_for_observer_report
from app.services.users import get_user_by_telegram_id

from .utils import get_current_user_and_admin, is_observer_role, row_get
from .views import (
    build_observer_report_text,
    send_observer_stats_menu,
    send_observer_tickets_page,
)

router = Router()


@router.message(F.text == "🟢 Активные тикеты")
async def observer_active_tickets_message(message: Message):
    await send_observer_tickets_page(message, list_type="active", page=0)

@router.message(F.text == "✅ Закрытые тикеты")
async def observer_closed_tickets_message(message: Message):
    await send_observer_tickets_page(message, list_type="closed", page=0)

@router.message(F.text == "📊 Статистика")
async def observer_stats_message(message: Message):
    await send_observer_stats_menu(message)

@router.callback_query(F.data.startswith("observer_active_tickets:"))
async def observer_active_tickets_callback(call: CallbackQuery):
    try:
        page = int(call.data.split(":")[1])
    except Exception:
        page = 0

    await send_observer_tickets_page(call, list_type="active", page=page)

@router.callback_query(F.data.startswith("observer_closed_tickets:"))
async def observer_closed_tickets_callback(call: CallbackQuery):
    try:
        page = int(call.data.split(":")[1])
    except Exception:
        page = 0

    await send_observer_tickets_page(call, list_type="closed", page=page)

@router.callback_query(F.data == "observer_stats_menu")
async def observer_stats_menu_callback(call: CallbackQuery):
    await send_observer_stats_menu(call)

@router.callback_query(F.data == "observer_stats_period_menu")
async def observer_stats_period_menu_callback(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user or not is_observer_role(row_get(user, "role")):
        await call.answer("Нет доступа.", show_alert=True)
        return

    await call.message.answer(
        "📅 Отчёт за период\n\nВыбери период.",
        reply_markup=observer_stats_period_keyboard()
    )
    await call.answer()

@router.callback_query(F.data.startswith("observer_stats_period:"))
async def observer_stats_period_callback(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user or not is_observer_role(row_get(user, "role")):
        await call.answer("Нет доступа.", show_alert=True)
        return

    period = call.data.split(":")[1]

    titles = {
        "day": "Отчёт за последние 24 часа",
        "week": "Отчёт за последние 7 дней",
        "month": "Отчёт за последние 30 дней",
    }

    report = await get_observer_report(period=period)
    text = build_observer_report_text(report, titles.get(period, "Отчёт за период"))

    await call.message.answer(text, reply_markup=observer_stats_menu_keyboard())
    await call.answer()

@router.callback_query(F.data == "observer_stats_all")
async def observer_stats_all_callback(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user or not is_observer_role(row_get(user, "role")):
        await call.answer("Нет доступа.", show_alert=True)
        return

    report = await get_observer_report(period=None)
    text = build_observer_report_text(report, "Отчёт за всё время")

    await call.message.answer(text, reply_markup=observer_stats_menu_keyboard())
    await call.answer()

@router.callback_query(F.data.startswith("observer_stats_users:"))
async def observer_stats_users_callback(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user or not is_observer_role(row_get(user, "role")):
        await call.answer("Нет доступа.", show_alert=True)
        return

    try:
        page = int(call.data.split(":")[1])
    except Exception:
        page = 0

    users = await get_users_for_observer_report()

    if not users:
        await call.message.answer("Пользователей для отчёта нет.", reply_markup=observer_stats_menu_keyboard())
        await call.answer()
        return

    total_pages = (len(users) + 10 - 1) // 10
    page = max(0, min(page, max(total_pages - 1, 0)))

    await call.message.answer(
        f"👤 Отчёт по пользователю\n\nВыбери пользователя. Страница {page + 1} из {total_pages}.",
        reply_markup=observer_users_keyboard(users, page=page, page_size=10)
    )
    await call.answer()

@router.callback_query(F.data.startswith("observer_stats_user:"))
async def observer_stats_user_callback(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)

    if not user or not is_observer_role(row_get(user, "role")):
        await call.answer("Нет доступа.", show_alert=True)
        return

    try:
        target_telegram_id = int(call.data.split(":")[1])
    except Exception:
        await call.answer("Некорректный пользователь.", show_alert=True)
        return

    target_user = await get_user_by_telegram_id(target_telegram_id)
    report = await get_observer_report(period=None, telegram_id=target_telegram_id)

    name = (
        row_get(target_user, "full_name")
        or row_get(target_user, "username")
        or target_telegram_id
    )

    text = build_observer_report_text(report, f"Отчёт по пользователю: {name}")

    await call.message.answer(text, reply_markup=observer_stats_menu_keyboard())
    await call.answer()
