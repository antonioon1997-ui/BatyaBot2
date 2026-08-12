from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from app.domain import department_by_role, is_observer_role, normalize_role
from app.services.ui_versions import compact_main_menu_enabled, help_button_enabled, pc_ticket_workspace_enabled

def row_get(row, key, default=None):
    if row is None:
        return default

    try:
        if key in row.keys():
            return row[key]
    except Exception:
        return default

    return default

def has_text_value(value) -> bool:
    if value is None:
        return False

    return bool(str(value).strip())

def ticket_open_button_text(ticket, prefix: str = "📂 Открыть") -> str:
    ticket_id = row_get(ticket, "id")
    order_number = row_get(ticket, "order_number")

    if has_text_value(order_number):
        return f"{prefix} #{ticket_id} (Заказ: {str(order_number).strip()})"

    return f"{prefix} #{ticket_id}"

def bottom_menu_for_role(role: str | None = None, is_admin: bool = False) -> ReplyKeyboardMarkup:
    if is_observer_role(role) and not is_admin:
        keyboard = [
            [
                KeyboardButton(text="🟢 Активные тикеты"),
            ],
            [
                KeyboardButton(text="✅ Закрытые тикеты"),
            ],
            [
                KeyboardButton(text="📊 Статистика"),
            ],
        ]
        if help_button_enabled():
            keyboard.append([KeyboardButton(text="❓ Помощь")])

        return ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True,
            is_persistent=True,
            one_time_keyboard=False,
            input_field_placeholder="Выбери действие..."
        )

    if compact_main_menu_enabled():
        # PC-first: ReplyKeyboard повторяет геометрию главного inline-меню.
        keyboard = [
            [KeyboardButton(text="➕ Создать тикет")],
        ]
        if pc_ticket_workspace_enabled():
            keyboard.append([
                KeyboardButton(text="📂 Работа с тикетами"),
                KeyboardButton(text="🔎 Узнать статус заказа"),
            ])
        else:
            keyboard.append([KeyboardButton(text="🔎 Узнать статус заказа")])
        navigation_row = [KeyboardButton(text="🏠 Меню")]
        if help_button_enabled():
            navigation_row.append(KeyboardButton(text="❓ Помощь"))
        keyboard.append(navigation_row)
    else:
        keyboard = [
            [KeyboardButton(text="➕ Создать тикет")],
            [KeyboardButton(text="🔎 Узнать статус заказа")],
            [KeyboardButton(text="📤 Исходящие"), KeyboardButton(text="📥 Входящие")],
            [KeyboardButton(text="🛠 В работе"), KeyboardButton(text="📦 Архив")],
            [KeyboardButton(text="📌 Моя работа")],
        ]
        if help_button_enabled():
            keyboard.append([KeyboardButton(text="❓ Помощь")])

    if is_admin:
        keyboard.append(
            [
                KeyboardButton(text="⚙️ Админка"),
            ]
        )

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder="Выбери действие..."
    )

def access_request_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить: Отдел закупки",
                    callback_data=f"approve_user:purchaser:{telegram_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Одобрить: Клиентский отдел",
                    callback_data=f"approve_user:client:{telegram_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👁 Одобрить как наблюдателя",
                    callback_data=f"approve_user:observer:{telegram_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject_user:{telegram_id}"
                )
            ],
        ]
    )

def main_menu_for_role(role: str | None = None, is_admin: bool = False) -> InlineKeyboardMarkup:
    if is_observer_role(role) and not is_admin:
        rows = [
            [
                InlineKeyboardButton(
                    text="🟢 Активные тикеты",
                    callback_data="observer_active_tickets:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Закрытые тикеты",
                    callback_data="observer_closed_tickets:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="observer_stats_menu"
                )
            ],
        ]
        if help_button_enabled():
            rows.append([InlineKeyboardButton(text="❓ Помощь", callback_data="help_main")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if compact_main_menu_enabled():
        # Inline-панель — рабочее меню, поэтому не содержит кнопку «Меню»,
        # которая в этом экране вела бы сама на себя. Постоянная ReplyKeyboard
        # остаётся отдельным быстрым пультом и сохраняет прежнюю геометрию.
        keyboard = [
            [InlineKeyboardButton(text="➕ Создать тикет", callback_data="create_ticket")],
            [InlineKeyboardButton(text="🔎 Узнать статус заказа", callback_data="order_status_start")],
        ]
        if pc_ticket_workspace_enabled():
            work_row = [InlineKeyboardButton(text="📂 Работа с тикетами", callback_data="ticket_work_menu")]
            if help_button_enabled():
                work_row.append(InlineKeyboardButton(text="❓ Помощь", callback_data="help_main"))
            keyboard.append(work_row)
        elif help_button_enabled():
            keyboard.append([InlineKeyboardButton(text="❓ Помощь", callback_data="help_main")])
    else:
        keyboard = [
            [InlineKeyboardButton(text="➕ Создать тикет", callback_data="create_ticket")],
            [InlineKeyboardButton(text="🔎 Узнать статус заказа", callback_data="order_status_start")],
            [
                InlineKeyboardButton(text="📤 Исходящие", callback_data="outgoing_tickets"),
                InlineKeyboardButton(text="📥 Входящие", callback_data="incoming_tickets"),
            ],
            [
                InlineKeyboardButton(text="🛠 В работе", callback_data="work_tickets"),
                InlineKeyboardButton(text="📦 Архив", callback_data="archive_tickets"),
            ],
            [InlineKeyboardButton(text="📌 Моя работа", callback_data="work_hub")],
        ]
        if help_button_enabled():
            keyboard.append([InlineKeyboardButton(text="❓ Помощь", callback_data="help_main")])

    if is_admin:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Админка",
                    callback_data="admin_menu"
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def ticket_work_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Исходящие", callback_data="outgoing_tickets"),
                InlineKeyboardButton(text="📥 Входящие", callback_data="incoming_tickets"),
            ],
            [
                InlineKeyboardButton(text="🛠 В работе", callback_data="work_tickets"),
                InlineKeyboardButton(text="📦 Архив", callback_data="archive_tickets"),
            ],
            [InlineKeyboardButton(text="📌 Моя работа", callback_data="work_hub")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return main_menu_for_role()


def compact_ticket_button_text(ticket) -> str:
    """Короткая подпись тикета для размещения нескольких кнопок в ряд."""
    ticket_id = row_get(ticket, "id")
    unread_prefix = "🔵 " if row_get(ticket, "has_unread", 0) else ""
    order_number = row_get(ticket, "order_number")
    if has_text_value(order_number):
        order = str(order_number).strip()
        if len(order) > 12:
            order = order[:11] + "…"
        return f"{unread_prefix}#{ticket_id} · {order}"
    return f"{unread_prefix}Тикет #{ticket_id}"

def compact_button_rows(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Группирует кнопки по 2–4 в ряд с учётом длины подписей."""
    rows = []
    current = []
    current_weight = 0
    for button in buttons:
        length = len(button.text or "")
        weight = 3 if length > 28 else 2 if length > 17 else 1
        max_count = 2 if weight == 3 else 3 if weight == 2 else 4
        if current and (len(current) >= max_count or current_weight + weight > 4):
            rows.append(current)
            current = []
            current_weight = 0
        current.append(button)
        current_weight += weight
    if current:
        rows.append(current)
    return rows

