from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from .common import access_request_keyboard, compact_button_rows, compact_ticket_button_text

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 Заявки на доступ",
                    callback_data="admin_access_requests"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Пользователи",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎫 Все тикеты",
                    callback_data="admin_tickets"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Напомнить о тикетах",
                    callback_data="admin_ticket_reminders"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Время напоминаний",
                    callback_data="admin_reminder_time"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Мини-статистика",
                    callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📈 Статистика и экспорт",
                    callback_data="admin_analytics"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Шаблоны закупки",
                    callback_data="admin_templates"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 Заметки об обновлениях",
                    callback_data="admin_notes"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Обновление бота",
                    callback_data="admin_bot_update"
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

def admin_ticket_reminder_departments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="В отдел закупки",
                    callback_data="admin_ticket_reminder_department:purchasing",
                )
            ],
            [
                InlineKeyboardButton(
                    text="В клиентский отдел",
                    callback_data="admin_ticket_reminder_department:client",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin_menu")],
        ]
    )


def admin_ticket_reminder_categories_keyboard(department: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Не в работе",
                    callback_data=f"admin_ticket_reminder_send:{department}:new",
                )
            ],
            [
                InlineKeyboardButton(
                    text="В работе",
                    callback_data=f"admin_ticket_reminder_send:{department}:work",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Выбор отдела",
                    callback_data="admin_ticket_reminders",
                )
            ],
        ]
    )


def manual_ticket_reminder_open_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Открыть входящие тикеты",
                    callback_data="incoming_tickets",
                )
            ]
        ]
    )


def admin_access_requests_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🆕 Новые заявки",
                    callback_data="admin_access_requests:new"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Одобренные заявки",
                    callback_data="admin_access_requests:approved"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонённые заявки",
                    callback_data="admin_access_requests:rejected"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Все заявки",
                    callback_data="admin_access_requests:all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Админка",
                    callback_data="admin_menu"
                )
            ],
        ]
    )

def admin_users_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Активные пользователи",
                    callback_data="admin_users:active"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Отключённые пользователи",
                    callback_data="admin_users:inactive"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Все пользователи",
                    callback_data="admin_users:all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Админка",
                    callback_data="admin_menu"
                )
            ],
        ]
    )

def admin_tickets_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Все видимые тикеты",
                    callback_data="admin_tickets:all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 Открытые тикеты",
                    callback_data="admin_tickets:open"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Закрытые тикеты",
                    callback_data="admin_tickets:closed"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалённые тикеты",
                    callback_data="admin_tickets:deleted"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Админка",
                    callback_data="admin_menu"
                )
            ],
        ]
    )

def admin_access_request_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return access_request_keyboard(telegram_id)

def admin_access_requests_list_keyboard(requests) -> InlineKeyboardMarkup:
    keyboard = []

    for request in requests:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"📂 Заявка #{request['id']} — {request['telegram_id']}",
                    callback_data=f"admin_request:{request['id']}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Заявки",
                callback_data="admin_access_requests"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_request_card_keyboard(request) -> InlineKeyboardMarkup:
    keyboard = []

    if request["status"] == "new":
        keyboard.extend(access_request_keyboard(request["telegram_id"]).inline_keyboard)

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Заявки",
                callback_data="admin_access_requests"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def users_list_keyboard(users) -> InlineKeyboardMarkup:
    keyboard = []

    for user in users:
        status_icon = "✅" if user["is_active"] == 1 else "🚫"
        name = user["full_name"] or user["username"] or user["telegram_id"]

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{status_icon} {name} — {user['role']}",
                    callback_data=f"admin_user:{user['telegram_id']}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Пользователи",
                callback_data="admin_users"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def user_admin_keyboard(user_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                text="🔄 Сменить роль",
                callback_data=f"change_role:{user_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🎫 Тикеты пользователя",
                callback_data=f"admin_user_tickets:{user_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏖 Выходной сегодня",
                callback_data=f"admin_dayoff_today:{user_id}"
            ),
            InlineKeyboardButton(
                text="🟢 Убрать выходной",
                callback_data=f"admin_dayoff_clear:{user_id}"
            )
        ],
    ]

    if is_active:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🚫 Отключить пользователя",
                    callback_data=f"deactivate_user:{user_id}"
                )
            ]
        )
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="✅ Восстановить пользователя",
                    callback_data=f"restore_user:{user_id}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к пользователям",
                callback_data="admin_users"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def change_role_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Отдел закупки",
                    callback_data=f"set_role:{user_id}:purchaser"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Клиентский отдел",
                    callback_data=f"set_role:{user_id}:client"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👁 Наблюдатель",
                    callback_data=f"set_role:{user_id}:observer"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"admin_user:{user_id}"
                )
            ],
        ]
    )

def admin_tickets_list_keyboard(tickets) -> InlineKeyboardMarkup:
    keyboard = []

    ticket_buttons = []
    for ticket in tickets:
        deleted_icon = "🗑" if ticket["is_deleted"] == 1 else "📂"
        ticket_buttons.append(
            InlineKeyboardButton(
                text=("🗑 " if ticket["is_deleted"] == 1 else "") + compact_ticket_button_text(ticket),
                callback_data=f"admin_ticket_open:{ticket['id']}"
            )
        )
    keyboard.extend(compact_button_rows(ticket_buttons))

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Тикеты",
                callback_data="admin_tickets"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_tickets_paginated_keyboard(
    tickets,
    ticket_filter: str = "all",
    page: int = 0,
    total: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    keyboard = []

    ticket_buttons = []
    for ticket in tickets:
        deleted_icon = "🗑" if ticket["is_deleted"] == 1 else "📂"
        ticket_buttons.append(
            InlineKeyboardButton(
                text=("🗑 " if ticket["is_deleted"] == 1 else "") + compact_ticket_button_text(ticket),
                callback_data=f"admin_ticket_open:{ticket['id']}"
            )
        )
    keyboard.extend(compact_button_rows(ticket_buttons))

    if total <= 0:
        total = len(tickets)

    total_pages = (total + page_size - 1) // page_size if total else 1

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    navigation = []

    if total_pages > 1:
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"admin_tickets:{ticket_filter}:{page - 1}"
                )
            )

        navigation.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=f"admin_tickets:{ticket_filter}:{page}"
            )
        )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="Вперёд ➡️",
                    callback_data=f"admin_tickets:{ticket_filter}:{page + 1}"
                )
            )

    if navigation:
        keyboard.append(navigation)

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Тикеты",
                callback_data="admin_tickets"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_ticket_action_keyboard(ticket) -> InlineKeyboardMarkup:
    ticket_id = int(ticket["id"])
    is_deleted = int(ticket["is_deleted"]) == 1

    keyboard = [
        [
            InlineKeyboardButton(
                text="💬 Админ-комментарий",
                callback_data=f"ticket_comment:{ticket_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔄 Сменить статус",
                callback_data=f"admin_ticket_status:{ticket_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="👤 Назначить исполнителя",
                callback_data=f"admin_ticket_assign_menu:{ticket_id}"
            )
        ],
    ]

    if is_deleted:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="♻️ Восстановить тикет",
                    callback_data=f"admin_ticket_restore:{ticket_id}"
                )
            ]
        )
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="🗑 Мягко удалить тикет",
                    callback_data=f"admin_ticket_delete:{ticket_id}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Тикеты",
                callback_data="admin_tickets"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_ticket_status_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🆕 Новый",
                    callback_data=f"admin_ticket_set_status:{ticket_id}:new"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛠 В работе",
                    callback_data=f"admin_ticket_set_status:{ticket_id}:in_work"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Ожидает подтверждения",
                    callback_data=f"admin_ticket_set_status:{ticket_id}:waiting_confirmation"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏁 Закрыт",
                    callback_data=f"admin_ticket_set_status:{ticket_id}:done"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменён",
                    callback_data=f"admin_ticket_set_status:{ticket_id}:cancelled"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к тикету",
                    callback_data=f"admin_ticket_open:{ticket_id}"
                )
            ],
        ]
    )


def update_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Установить обновление", callback_data="bot_update_install"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="bot_update_cancel"),
            ],
        ]
    )


def admin_notes_keyboard(notes) -> InlineKeyboardMarkup:
    rows = []
    for note in notes:
        icon = "✅" if note["status"] == "done" else "📝"
        title = str(note["title"] or "Без названия")[:45]
        rows.append([InlineKeyboardButton(text=f"{icon} {title}", callback_data=f"admin_note:{note['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Новая заметка", callback_data="admin_note_add")])
    rows.append([InlineKeyboardButton(text="📥 Скачать заметки", callback_data="admin_notes_export")])
    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_note_card_keyboard(note_id: int, status: str) -> InlineKeyboardMarkup:
    status_text = "↩️ Вернуть в планы" if status == "done" else "✅ Отметить выполненной"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"admin_note_edit_title:{note_id}")],
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data=f"admin_note_edit_body:{note_id}")],
        [InlineKeyboardButton(text=status_text, callback_data=f"admin_note_toggle:{note_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_note_delete:{note_id}")],
        [InlineKeyboardButton(text="⬅️ К заметкам", callback_data="admin_notes")],
    ])
