from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from app.config import settings
from app.keyboards.ui_metrics import (
    ui_metrics_departments_keyboard,
    ui_metrics_menu_keyboard,
    ui_metrics_users_keyboard,
)
from app.services.ui_metrics import (
    export_ui_metrics_csv,
    get_button_summary,
    get_department_totals,
    get_unused_main_buttons,
    get_user_totals,
)
from app.services.users import get_user_by_telegram_id
from app.utils import html_escape

router = Router()

DEPARTMENT_NAMES = {
    "client": "Клиентский отдел",
    "purchasing": "Отдел закупки",
    "observer": "Наблюдатели",
    "unknown": "Не определён",
}


def _is_admin(user_id: int) -> bool:
    return int(user_id) == int(settings.admin_id)


async def _deny(call: CallbackQuery) -> bool:
    if _is_admin(call.from_user.id):
        return False
    await call.answer("Нет доступа.", show_alert=True)
    return True


def _period_title(days: int) -> str:
    return f"последние {days} дней"


def _summary_lines(rows, *, empty_text: str = "Данных пока нет.") -> str:
    if not rows:
        return empty_text
    lines = []
    for index, row in enumerate(rows, start=1):
        label = row["button_text"] or row["button_id"]
        lines.append(
            f"{index}. <b>{html_escape(label)}</b>\n"
            f"   Нажатий: {int(row['clicks'] or 0)} · пользователей: {int(row['unique_users'] or 0)}\n"
            f"   Последнее: {html_escape(row['last_click_at'] or '—')}"
        )
    return "\n\n".join(lines)


async def _send_period_report(call: CallbackQuery, days: int) -> None:
    rows = await get_button_summary(days=days, scope="main", limit=30)
    total_clicks = sum(int(row["clicks"] or 0) for row in rows)
    unique_buttons = len(rows)
    await call.message.answer(
        "🖱 <b>Метрики кнопок главного меню</b>\n\n"
        f"Период: <b>{_period_title(days)}</b>\n"
        f"Всего нажатий: <b>{total_clicks}</b>\n"
        f"Использовано кнопок: <b>{unique_buttons}</b>\n\n"
        f"{_summary_lines(rows)}\n\n"
        "Считаются нажатия нижней клавиатуры и её inline-дубликатов. "
        "Служебные действия внутри тикетов в эту сводку не входят, но сохраняются в CSV.",
        reply_markup=ui_metrics_menu_keyboard(),
    )


@router.callback_query(F.data == "admin_ui_metrics")
async def admin_ui_metrics_callback(call: CallbackQuery):
    if await _deny(call):
        return
    await _send_period_report(call, 7)
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_metrics_period:"))
async def admin_ui_metrics_period_callback(call: CallbackQuery):
    if await _deny(call):
        return
    try:
        days = int(call.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        days = 7
    days = 30 if days == 30 else 7
    await _send_period_report(call, days)
    await call.answer()


@router.callback_query(F.data == "admin_ui_metrics_departments")
async def admin_ui_metrics_departments_callback(call: CallbackQuery):
    if await _deny(call):
        return
    rows = await get_department_totals(days=30, scope="main")
    if rows:
        lines = []
        for row in rows:
            department = str(row["department"] or "unknown")
            lines.append(
                f"<b>{html_escape(DEPARTMENT_NAMES.get(department, department))}</b>\n"
                f"Нажатий: {int(row['clicks'] or 0)} · сотрудников: {int(row['unique_users'] or 0)} · "
                f"кнопок: {int(row['unique_buttons'] or 0)}"
            )
        body = "\n\n".join(lines)
    else:
        body = "Данных пока нет."
    await call.message.answer(
        "🏢 <b>Использование главного меню по отделам</b>\n\n"
        "Период: последние 30 дней.\n\n"
        f"{body}\n\n"
        "Выберите отдел, чтобы увидеть распределение по кнопкам.",
        reply_markup=ui_metrics_departments_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_metrics_department:"))
async def admin_ui_metrics_department_callback(call: CallbackQuery):
    if await _deny(call):
        return
    parts = call.data.split(":")
    department = parts[1] if len(parts) > 1 else "unknown"
    try:
        days = int(parts[2]) if len(parts) > 2 else 30
    except ValueError:
        days = 30
    rows = await get_button_summary(days=days, department=department, scope="main", limit=30)
    await call.message.answer(
        f"🏢 <b>{html_escape(DEPARTMENT_NAMES.get(department, department))}</b>\n\n"
        f"Период: {_period_title(days)}.\n\n"
        f"{_summary_lines(rows)}",
        reply_markup=ui_metrics_departments_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin_ui_metrics_users")
async def admin_ui_metrics_users_callback(call: CallbackQuery):
    if await _deny(call):
        return
    users = await get_user_totals(days=30, scope="main")
    lines = []
    for user in users:
        name = user["full_name"] or user["username"] or str(user["telegram_id"])
        lines.append(
            f"• <b>{html_escape(name)}</b>: {int(user['clicks'] or 0)} нажатий, "
            f"{int(user['unique_buttons'] or 0)} разных кнопок"
        )
    await call.message.answer(
        "👤 <b>Использование меню по сотрудникам</b>\n\n"
        "Период: последние 30 дней.\n\n"
        + ("\n".join(lines) if lines else "Данных пока нет.")
        + "\n\nНажмите на сотрудника, чтобы увидеть его распределение по кнопкам.",
        reply_markup=ui_metrics_users_keyboard(users),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_metrics_user:"))
async def admin_ui_metrics_user_callback(call: CallbackQuery):
    if await _deny(call):
        return
    parts = call.data.split(":")
    try:
        user_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
    except (ValueError, IndexError):
        await call.answer("Некорректный пользователь.", show_alert=True)
        return
    user = await get_user_by_telegram_id(user_id)
    if not user:
        await call.answer("Пользователь не найден.", show_alert=True)
        return
    rows = await get_button_summary(days=days, user_id=user_id, scope="main", limit=30)
    name = user["full_name"] or user["username"] or str(user_id)
    users = await get_user_totals(days=30, scope="main")
    await call.message.answer(
        f"👤 <b>{html_escape(name)}</b>\n\n"
        f"Период: {_period_title(days)}.\n\n"
        f"{_summary_lines(rows)}",
        reply_markup=ui_metrics_users_keyboard(users),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_metrics_rare:"))
async def admin_ui_metrics_rare_callback(call: CallbackQuery):
    if await _deny(call):
        return
    try:
        days = int(call.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        days = 30
    rows = await get_button_summary(days=days, scope="main", order="asc", limit=30)
    await call.message.answer(
        "🔻 <b>Редко используемые кнопки</b>\n\n"
        f"Период: {_period_title(days)}. Кнопки отсортированы от самых редких.\n\n"
        f"{_summary_lines(rows)}\n\n"
        "Редкое использование не всегда означает, что кнопка не нужна: функция может быть редкой, но критичной.",
        reply_markup=ui_metrics_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_metrics_unused:"))
async def admin_ui_metrics_unused_callback(call: CallbackQuery):
    if await _deny(call):
        return
    try:
        days = int(call.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        days = 30
    buttons = await get_unused_main_buttons(days=days)
    lines = "\n".join(f"• {html_escape(label)}" for _, label in buttons) or "Все отслеживаемые кнопки использовались."
    await call.message.answer(
        "🚫 <b>Кнопки без нажатий</b>\n\n"
        f"Период: {_period_title(days)}.\n\n"
        f"{lines}\n\n"
        "Перед скрытием кнопки стоит проверить, знают ли сотрудники о её назначении.",
        reply_markup=ui_metrics_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin_ui_metrics_export")
async def admin_ui_metrics_export_callback(call: CallbackQuery):
    if await _deny(call):
        return
    payload = await export_ui_metrics_csv(days=365)
    filename = f"batyabot_button_metrics_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await call.message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption=(
            "📄 Метрики нажатий за последние 365 дней. "
            "В CSV входят главное меню, служебные inline-кнопки, пользователь, отдел и версия интерфейса."
        ),
    )
    await call.answer()
