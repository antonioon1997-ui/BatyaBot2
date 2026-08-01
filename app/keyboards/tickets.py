from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from .common import compact_button_rows, compact_ticket_button_text, department_by_role, is_observer_role, row_get

def cancel_create_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить создание",
                    callback_data="cancel_create_ticket"
                )
            ]
        ]
    )

def after_ticket_text_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="cancel_create_ticket"
                )
            ]
        ]
    )

def attachments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="cancel_create_ticket"
                )
            ]
        ]
    )

def open_ticket_keyboard(ticket_id: int, *, can_cancel: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if can_cancel:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Закрыть как неактуальный",
                    callback_data=f"ticket_cancel:{ticket_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="📂 Открыть тикет",
                callback_data=f"ticket_open:{ticket_id}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ticket_notification_keyboard(ticket, user=None) -> InlineKeyboardMarkup:
    user_id = int(row_get(user, "telegram_id", 0) or 0)
    creator_id = int(row_get(ticket, "created_by", 0) or 0)
    status = str(row_get(ticket, "status", ""))
    return open_ticket_keyboard(
        int(row_get(ticket, "id")),
        can_cancel=(user_id == creator_id and status not in {"done", "cancelled"}),
    )


def delayed_close_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Продолжить обсуждение",
                    callback_data=f"ticket_continue_auto_close:{ticket_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Закрыть сейчас",
                    callback_data=f"ticket_close_now:{ticket_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть как неактуальный",
                    callback_data=f"ticket_cancel:{ticket_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📂 Открыть тикет",
                    callback_data=f"ticket_open:{ticket_id}",
                )
            ],
        ]
    )

def tickets_list_keyboard(tickets, list_type: str | None = None) -> InlineKeyboardMarkup:
    keyboard = []

    ticket_buttons = [
        InlineKeyboardButton(
            text=compact_ticket_button_text(ticket),
            callback_data=f"ticket_open:{ticket['id']}"
        )
        for ticket in tickets
    ]
    keyboard.extend(compact_button_rows(ticket_buttons))

    if list_type:
        keyboard.append([InlineKeyboardButton(text="🔽 Фильтры", callback_data=f"ticket_filters:{list_type}")])

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="main_menu"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def archive_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Архив входящих",
                    callback_data="archive_page:incoming:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📤 Архив исходящих",
                    callback_data="archive_page:outgoing:0"
                )
            ],
            [InlineKeyboardButton(text="🔽 Фильтры архива", callback_data="ticket_filters:archive")],
            [
                InlineKeyboardButton(
                    text="🔎 Поиск по архиву",
                    callback_data="archive_search"
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

def archive_tickets_keyboard(
    tickets,
    archive_type: str,
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
                    callback_data=f"archive_page:{archive_type}:{page - 1}"
                )
            )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"archive_page:{archive_type}:{page + 1}"
                )
            )

    if navigation:
        keyboard.append(navigation)

    keyboard.append([InlineKeyboardButton(text="🔽 Фильтры архива", callback_data="ticket_filters:archive")])

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ К выбору архива",
                callback_data="archive_tickets"
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="main_menu"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def archive_search_results_keyboard(
    tickets,
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
                    callback_data=f"archive_search_page:{page - 1}"
                )
            )

        navigation.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=f"archive_search_page:{page}"
            )
        )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"archive_search_page:{page + 1}"
                )
            )

    if navigation:
        keyboard.append(navigation)

    keyboard.append(
        [
            InlineKeyboardButton(
                text="🔎 Новый поиск",
                callback_data="archive_search"
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="main_menu"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def overdue_tickets_keyboard(tickets, page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
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
                    text="⬅️ Назад",
                    callback_data=f"overdue_page:{page - 1}"
                )
            )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="Вперёд ➡️",
                    callback_data=f"overdue_page:{page + 1}"
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

def ticket_action_keyboard(ticket, user=None, is_admin: bool = False) -> InlineKeyboardMarkup:
    ticket_id = int(ticket["id"])
    status = str(ticket["status"])

    user_id = None
    user_role = None

    if user:
        user_id = int(user["telegram_id"])
        user_role = user["role"]

    created_by = int(ticket["created_by"])
    executor_department = ticket["executor_department"]
    requester_department = row_get(ticket, "requester_department")
    user_department = department_by_role(user_role)

    is_creator = user_id == created_by
    is_executor_department = user_department == executor_department
    is_observer = is_observer_role(user_role)
    is_client_department = user_department == "client"
    is_participant_department = user_department in {requester_department, executor_department}

    keyboard = []

    if is_admin:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Открыть в админке",
                    callback_data=f"admin_ticket_open:{ticket_id}"
                )
            ]
        )

    if is_observer:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="main_menu"
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    if status in {"new", "in_work"}:
        if is_creator:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="❌ Закрыть как неактуальный",
                        callback_data=f"ticket_cancel:{ticket_id}"
                    )
                ]
            )

        if is_executor_department:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="💬 Ответить",
                        callback_data=f"ticket_comment:{ticket_id}"
                    )
                ]
            )

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="✅ Ответить и выполнить",
                        callback_data=f"ticket_comment_done:{ticket_id}"
                    )
                ]
            )

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="🏁 Выполнить",
                        callback_data=f"ticket_resolve:{ticket_id}"
                    )
                ]
            )

        if is_creator:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="💬 Дополнить тикет",
                        callback_data=f"ticket_comment:{ticket_id}"
                    )
                ]
            )

    elif status == "waiting_confirmation":
        is_delayed_close = bool(row_get(ticket, "auto_close_at"))

        if is_creator:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="❌ Закрыть как неактуальный",
                        callback_data=f"ticket_cancel:{ticket_id}"
                    )
                ]
            )

        if is_creator and is_delayed_close:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="💬 Продолжить обсуждение",
                        callback_data=f"ticket_continue_auto_close:{ticket_id}"
                    )
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="✅ Закрыть сейчас",
                        callback_data=f"ticket_close_now:{ticket_id}"
                    )
                ]
            )
        elif is_creator:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить выполнение",
                        callback_data=f"ticket_confirm_close:{ticket_id}"
                    )
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="↩️ Вернуть в работу",
                        callback_data=f"ticket_return:{ticket_id}"
                    )
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="💬 Дополнить тикет",
                        callback_data=f"ticket_comment:{ticket_id}"
                    )
                ]
            )

        if is_executor_department:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="💬 Ответить",
                        callback_data=f"ticket_comment:{ticket_id}"
                    )
                ]
            )

    elif status in {"done", "cancelled"}:
        if is_client_department:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="↩️ Вернуть в работу",
                        callback_data=f"ticket_return:{ticket_id}"
                    )
                ]
            )

    if status not in {"done", "cancelled"}:
        taken_by = row_get(ticket, "taken_by")
        workflow_row = []
        if is_admin:
            workflow_row.append(
                InlineKeyboardButton(
                    text="👤 Назначить",
                    callback_data=f"admin_ticket_assign_menu:{ticket_id}"
                )
            )
        elif is_executor_department:
            if not taken_by:
                workflow_row.append(
                    InlineKeyboardButton(
                        text="👤 Назначить себя",
                        callback_data=f"ticket_assign_self:{ticket_id}"
                    )
                )
            elif int(taken_by) == int(user_id or 0):
                workflow_row.append(
                    InlineKeyboardButton(
                        text="🔄 Передать",
                        callback_data=f"ticket_transfer_menu:{ticket_id}"
                    )
                )
            else:
                workflow_row.append(
                    InlineKeyboardButton(
                        text="🙋 Запросить себе",
                        callback_data=f"ticket_transfer_request:{ticket_id}"
                    )
                )
        if is_admin or is_executor_department:
            workflow_row.append(
                InlineKeyboardButton(
                    text="📝 Итог",
                    callback_data=f"ticket_summary_menu:{ticket_id}"
                )
            )
        if workflow_row:
            keyboard.append(workflow_row)

        if executor_department == "purchasing" and (is_admin or is_executor_department):
            snoozed = bool(row_get(ticket, "snoozed_until"))
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="⏰ Изменить отложение" if snoozed else "⏰ Отложить",
                        callback_data=f"ticket_snooze_menu:{ticket_id}"
                    )
                ]
            )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="main_menu"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def open_unprocessed_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📂 Открыть входящие",
                    callback_data="incoming_tickets"
                )
            ]
        ]
    )

def post_create_options_keyboard(ticket_id: int, priority: str = "normal", category: str | None = None) -> InlineKeyboardMarkup:
    priority_labels = {"normal": "🟢 Обычный", "important": "🟡 Важный", "urgent": "🔴 Срочный"}
    category_labels = {None: "не выбран", "question": "Вопрос", "task": "Задача", "problem": "Проблема", "documents": "Документы"}
    rows = [[InlineKeyboardButton(text="При необходимости укажите срочность:", callback_data="noop")]]
    rows.append([InlineKeyboardButton(text=("✓ " if priority == value else "") + label, callback_data=f"ticket_priority:{ticket_id}:{value}") for value, label in priority_labels.items()])
    rows.append([InlineKeyboardButton(text=f"Тип тикета: {category_labels.get(category, 'не выбран')}", callback_data=f"ticket_category_menu:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="❌ Закрыть как неактуальный", callback_data=f"ticket_cancel:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="📂 Открыть тикет", callback_data=f"ticket_open:{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def ticket_category_keyboard(ticket_id: int, selected: str | None = None) -> InlineKeyboardMarkup:
    options = [("question", "❓ Вопрос"), ("task", "📝 Задача"), ("problem", "⚠️ Проблема"), ("documents", "📄 Документы") ]
    rows = [[InlineKeyboardButton(text=("✓ " if selected == value else "") + label, callback_data=f"ticket_category:{ticket_id}:{value}")] for value, label in options]
    rows.append([InlineKeyboardButton(text="Без типа", callback_data=f"ticket_category:{ticket_id}:none")])
    rows.append([InlineKeyboardButton(text="❌ Закрыть как неактуальный", callback_data=f"ticket_cancel:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ticket_options:{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def ticket_filters_keyboard(list_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новые", callback_data=f"ticket_filter:{list_type}:status:new"), InlineKeyboardButton(text="🛠 В работе", callback_data=f"ticket_filter:{list_type}:status:in_work")],
        [InlineKeyboardButton(text="⏳ Ждут подтверждения", callback_data=f"ticket_filter:{list_type}:status:waiting_confirmation"), InlineKeyboardButton(text="🏁 Закрытые", callback_data=f"ticket_filter:{list_type}:status:done")],
        [InlineKeyboardButton(text="🟢 Обычные", callback_data=f"ticket_filter:{list_type}:priority:normal"), InlineKeyboardButton(text="🟡 Важные", callback_data=f"ticket_filter:{list_type}:priority:important"), InlineKeyboardButton(text="🔴 Срочные", callback_data=f"ticket_filter:{list_type}:priority:urgent")],
        [InlineKeyboardButton(text="❓ Вопросы", callback_data=f"ticket_filter:{list_type}:category:question"), InlineKeyboardButton(text="📝 Задачи", callback_data=f"ticket_filter:{list_type}:category:task")],
        [InlineKeyboardButton(text="⚠️ Проблемы", callback_data=f"ticket_filter:{list_type}:category:problem"), InlineKeyboardButton(text="📄 Документы", callback_data=f"ticket_filter:{list_type}:category:documents")],
        [InlineKeyboardButton(text="📎 С вложениями", callback_data=f"ticket_filter:{list_type}:attachments:yes"), InlineKeyboardButton(text="Без вложений", callback_data=f"ticket_filter:{list_type}:attachments:no")],
        [InlineKeyboardButton(text="📅 За 7 дней", callback_data=f"ticket_filter:{list_type}:date:7"), InlineKeyboardButton(text="📅 За 30 дней", callback_data=f"ticket_filter:{list_type}:date:30")],
        [InlineKeyboardButton(text="⏰ Просроченные", callback_data=f"ticket_filter:{list_type}:overdue:yes")],
        [InlineKeyboardButton(text="Отдел закупки", callback_data=f"ticket_filter:{list_type}:department:purchasing"), InlineKeyboardButton(text="Клиентский отдел", callback_data=f"ticket_filter:{list_type}:department:client")],
        [InlineKeyboardButton(text="🧹 Без фильтра", callback_data=f"ticket_filter:{list_type}:clear:all"), InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
    ])
