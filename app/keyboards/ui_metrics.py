from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def ui_metrics_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="7 дней", callback_data="admin_ui_metrics_period:7"),
                InlineKeyboardButton(text="30 дней", callback_data="admin_ui_metrics_period:30"),
            ],
            [
                InlineKeyboardButton(text="🏢 По отделам", callback_data="admin_ui_metrics_departments"),
                InlineKeyboardButton(text="👤 По сотрудникам", callback_data="admin_ui_metrics_users"),
            ],
            [
                InlineKeyboardButton(text="🔻 Редко нажимают", callback_data="admin_ui_metrics_rare:30"),
                InlineKeyboardButton(text="🚫 Не нажимали", callback_data="admin_ui_metrics_unused:30"),
            ],
            [InlineKeyboardButton(text="📄 Выгрузить CSV", callback_data="admin_ui_metrics_export")],
            [InlineKeyboardButton(text="⬅️ К аналитике", callback_data="admin_analytics")],
        ]
    )


def ui_metrics_departments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Клиентский отдел", callback_data="admin_ui_metrics_department:client:30")],
            [InlineKeyboardButton(text="Отдел закупки", callback_data="admin_ui_metrics_department:purchasing:30")],
            [InlineKeyboardButton(text="Наблюдатели", callback_data="admin_ui_metrics_department:observer:30")],
            [InlineKeyboardButton(text="Не определён", callback_data="admin_ui_metrics_department:unknown:30")],
            [InlineKeyboardButton(text="⬅️ Метрики кнопок", callback_data="admin_ui_metrics")],
        ]
    )


def ui_metrics_users_keyboard(users) -> InlineKeyboardMarkup:
    rows = []
    for user in users:
        name = user["full_name"] or user["username"] or str(user["telegram_id"])
        clicks = int(user["clicks"] or 0)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{name} · {clicks}",
                    callback_data=f"admin_ui_metrics_user:{int(user['telegram_id'])}:30",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Метрики кнопок", callback_data="admin_ui_metrics")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
