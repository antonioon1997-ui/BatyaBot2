from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from .common import compact_button_rows, compact_ticket_button_text, row_get

def observer_tickets_keyboard(
    tickets,
    list_type: str,
    page: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    total = len(tickets)

    if page < 0:
        page = 0

    total_pages = (total + page_size - 1) // page_size if total else 1

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    start = page * page_size
    end = start + page_size
    page_tickets = tickets[start:end]

    keyboard = []

    ticket_buttons = [
        InlineKeyboardButton(
            text=compact_ticket_button_text(ticket),
            callback_data=f"ticket_open:{ticket['id']}"
        )
        for ticket in page_tickets
    ]
    keyboard.extend(compact_button_rows(ticket_buttons))

    navigation = []

    if total_pages > 1:
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"observer_{list_type}_tickets:{page - 1}"
                )
            )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"observer_{list_type}_tickets:{page + 1}"
                )
            )

    if navigation:
        keyboard.append(navigation)

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="main_menu"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def observer_stats_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Отчёт за период",
                    callback_data="observer_stats_period_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Отчёт за всё время",
                    callback_data="observer_stats_all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Отчёт по пользователю",
                    callback_data="observer_stats_users:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="main_menu"
                )
            ],
        ]
    )

def observer_stats_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="За день",
                    callback_data="observer_stats_period:day"
                )
            ],
            [
                InlineKeyboardButton(
                    text="За неделю",
                    callback_data="observer_stats_period:week"
                )
            ],
            [
                InlineKeyboardButton(
                    text="За месяц",
                    callback_data="observer_stats_period:month"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К статистике",
                    callback_data="observer_stats_menu"
                )
            ],
        ]
    )

def observer_users_keyboard(users, page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
    total = len(users)

    if page < 0:
        page = 0

    total_pages = (total + page_size - 1) // page_size if total else 1

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    start = page * page_size
    end = start + page_size
    page_users = users[start:end]

    keyboard = []

    for user in page_users:
        name = row_get(user, "full_name") or row_get(user, "username") or row_get(user, "telegram_id")
        role = row_get(user, "role") or "без роли"

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{name} — {role}",
                    callback_data=f"observer_stats_user:{row_get(user, 'telegram_id')}"
                )
            ]
        )

    navigation = []

    if total_pages > 1:
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"observer_stats_users:{page - 1}"
                )
            )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"observer_stats_users:{page + 1}"
                )
            )

    if navigation:
        keyboard.append(navigation)

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ К статистике",
                callback_data="observer_stats_menu"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
