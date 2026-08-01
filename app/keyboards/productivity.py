from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils import format_moscow_datetime


def work_hub_keyboard(unread_count: int = 0) -> InlineKeyboardMarkup:
    unread_text = f"🔔 Непрочитанные ({unread_count})" if unread_count else "🔔 Непрочитанные"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Назначенные мне", callback_data="assigned_tickets"),
            InlineKeyboardButton(text="📋 Общие", callback_data="common_tickets"),
        ],
        [
            InlineKeyboardButton(text=unread_text, callback_data="unread_tickets"),
            InlineKeyboardButton(text="🔎 Поиск", callback_data="active_search"),
        ],
        [InlineKeyboardButton(text="🏖 Выходные", callback_data="day_off_menu")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
    ])


def assignment_candidates_keyboard(ticket_id: int, users, *, prefix: str, allow_common: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for user in users:
        name = user["full_name"] or user["username"] or str(user["telegram_id"])
        rows.append([InlineKeyboardButton(text=f"👤 {name}", callback_data=f"{prefix}:{ticket_id}:{user['telegram_id']}")])
    if allow_common:
        rows.append([InlineKeyboardButton(text="📋 Сделать общим", callback_data=f"{prefix}:{ticket_id}:0")])
    rows.append([InlineKeyboardButton(text="⬅️ К тикету", callback_data=f"ticket_open:{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def transfer_request_keyboard(request_id: int, ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Передать тикет", callback_data=f"transfer_approve:{request_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"transfer_reject:{request_id}")],
        [InlineKeyboardButton(text="📂 Открыть тикет", callback_data=f"ticket_open:{ticket_id}")],
    ])


def day_off_keyboard(has_day_off: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Сегодня: 1 день", callback_data="dayoff_set:0:1"),
         InlineKeyboardButton(text="Сегодня: 2 дня", callback_data="dayoff_set:0:2")],
        [InlineKeyboardButton(text="Сегодня: 3 дня", callback_data="dayoff_set:0:3")],
        [InlineKeyboardButton(text="С завтра: 1 день", callback_data="dayoff_set:1:1"),
         InlineKeyboardButton(text="С завтра: 2 дня", callback_data="dayoff_set:1:2")],
        [InlineKeyboardButton(text="С завтра: 3 дня", callback_data="dayoff_set:1:3")],
    ]
    if has_day_off:
        rows.append([InlineKeyboardButton(text="🟢 Убрать выходной", callback_data="dayoff_clear")])
    rows.append([InlineKeyboardButton(text="⬅️ Моя работа", callback_data="work_hub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def restore_day_off_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Вернуть свободные тикеты себе", callback_data=f"dayoff_restore:{user_id}")],
        [InlineKeyboardButton(text="Оставить общими", callback_data=f"dayoff_restore_skip:{user_id}")],
    ])


def duplicate_warning_keyboard(duplicates) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📂 Открыть тикет #{row['id']}", callback_data=f"ticket_open:{row['id']}")]
        for row in duplicates[:5]
    ]
    rows.append([InlineKeyboardButton(text="➕ Всё равно создать новый", callback_data="duplicate_create_confirm")])
    rows.append([InlineKeyboardButton(text="❌ Отменить создание", callback_data="cancel_create_ticket")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ticket_summary_keyboard(ticket_id: int, has_values: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📝 Изменить краткий итог", callback_data=f"ticket_summary_set:{ticket_id}:summary")],
        [InlineKeyboardButton(text="➡️ Изменить следующее действие", callback_data=f"ticket_summary_set:{ticket_id}:next")],
    ]
    if has_values:
        rows.append([InlineKeyboardButton(text="🧹 Очистить оба поля", callback_data=f"ticket_summary_clear:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ К тикету", callback_data=f"ticket_open:{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def snooze_keyboard(ticket_id: int, is_snoozed: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Через 1 час", callback_data=f"ticket_snooze_quick:{ticket_id}:1h"),
         InlineKeyboardButton(text="Через 3 часа", callback_data=f"ticket_snooze_quick:{ticket_id}:3h")],
        [InlineKeyboardButton(text="Завтра в 10:00", callback_data=f"ticket_snooze_quick:{ticket_id}:tomorrow10")],
        [InlineKeyboardButton(text="📅 Указать дату и время", callback_data=f"ticket_snooze_custom:{ticket_id}")],
    ]
    if is_snoozed:
        rows.append([InlineKeyboardButton(text="🟢 Вернуть сейчас", callback_data=f"ticket_snooze_clear:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ К тикету", callback_data=f"ticket_open:{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def response_templates_keyboard(ticket_id: int, close_after: bool, templates) -> InlineKeyboardMarkup:
    close_flag = 1 if close_after else 0
    rows = [[InlineKeyboardButton(text=f"💬 {row['title']}", callback_data=f"ticket_tpl:{ticket_id}:{row['id']}:{close_flag}")] for row in templates]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"ticket_open:{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def template_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="ticket_tpl_send")],
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="ticket_tpl_edit")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ticket_tpl_cancel")],
    ])


def admin_templates_keyboard(templates) -> InlineKeyboardMarkup:
    rows = []
    for row in templates:
        icon = "✅" if row["is_active"] else "🚫"
        rows.append([InlineKeyboardButton(text=f"{icon} {row['title']}", callback_data=f"admin_tpl:{row['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить шаблон", callback_data="admin_tpl_add")])
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_template_card_keyboard(template_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle = "🚫 Отключить" if is_active else "✅ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"admin_tpl_edit_title:{template_id}")],
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data=f"admin_tpl_edit_body:{template_id}")],
        [InlineKeyboardButton(text=toggle, callback_data=f"admin_tpl_toggle:{template_id}")],
        [InlineKeyboardButton(text="⬅️ Шаблоны", callback_data="admin_templates")],
    ])


def daily_summary_confirm_keyboard(stat_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить наблюдателям", callback_data=f"daily_summary_confirm:{stat_date}")],
        [InlineKeyboardButton(text="📊 Открыть аналитику", callback_data="admin_analytics")],
    ])


def admin_analytics_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить показатели", callback_data="admin_analytics")],
        [InlineKeyboardButton(text="🖱 Метрики кнопок", callback_data="admin_ui_metrics")],
        [InlineKeyboardButton(text="📄 Выгрузить CSV", callback_data="admin_stats_export")],
        [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin_menu")],
    ])
