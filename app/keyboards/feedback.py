from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.polls import parse_poll_options

CHOICE_LABELS = ["А", "Б", "В", "Г", "Д"]


def admin_feedback_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Новые сообщения", callback_data="admin_feedback_list:new")],
            [InlineKeyboardButton(text="📋 Все сообщения", callback_data="admin_feedback_list:all")],
            [InlineKeyboardButton(text="🗳 Создать голосование", callback_data="admin_poll_create")],
            [InlineKeyboardButton(text="📊 Голосования", callback_data="admin_polls")],
            [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin_menu")],
        ]
    )


def admin_feedback_list_keyboard(items, list_filter: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    status_icons = {"new": "🆕", "in_work": "🟡", "done": "✅"}
    for item in items:
        name = item["full_name"] or item["username"] or str(item["user_id"])
        preview = (item["text"] or "вложение").replace("\n", " ").strip()
        if len(preview) > 32:
            preview = preview[:31] + "…"
        icon = status_icons.get(item["status"], "💬")
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} #{item['id']} · {name}: {preview}",
                callback_data=f"admin_feedback_open:{item['id']}:{list_filter}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Обратная связь", callback_data="admin_feedback")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_feedback_card_keyboard(feedback_id: int, status: str, back_filter: str = "all") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status != "in_work":
        rows.append([InlineKeyboardButton(text="🟡 В работу", callback_data=f"admin_feedback_status:{feedback_id}:in_work:{back_filter}")])
    if status != "done":
        rows.append([InlineKeyboardButton(text="✅ Закрыть", callback_data=f"admin_feedback_status:{feedback_id}:done:{back_filter}")])
    if status != "new":
        rows.append([InlineKeyboardButton(text="↩️ Вернуть в новые", callback_data=f"admin_feedback_status:{feedback_id}:new:{back_filter}")])
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data=f"admin_feedback_list:{back_filter}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def poll_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔀 Выбрать вариант", callback_data="admin_poll_type:choice")],
            [InlineKeyboardButton(text="🔢 Оценить идею 1–5", callback_data="admin_poll_type:rating")],
            [InlineKeyboardButton(text="⬅️ Обратная связь", callback_data="admin_feedback")],
        ]
    )


def poll_none_label_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Оставить как есть", callback_data="admin_poll_none:keep")],
            [InlineKeyboardButton(text="🚫 Ничего из этого не нужно", callback_data="admin_poll_none:none")],
            [InlineKeyboardButton(text="✏️ Свой текст", callback_data="admin_poll_none:custom")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_poll_cancel")],
        ]
    )


def poll_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 Опубликовать", callback_data="admin_poll_publish")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_poll_cancel")],
        ]
    )


def user_poll_keyboard(poll, selected: str | None = None) -> InlineKeyboardMarkup:
    options = parse_poll_options(poll["options_json"])
    poll_id = int(poll["id"])
    buttons: list[InlineKeyboardButton] = []
    for index, _ in enumerate(options):
        label = str(index + 1) if poll["poll_type"] == "rating" else CHOICE_LABELS[index]
        if selected == str(index):
            label = "✅ " + label
        buttons.append(InlineKeyboardButton(text=label, callback_data=f"poll_vote:{poll_id}:{index}"))

    rows: list[list[InlineKeyboardButton]] = [buttons]
    none_text = str(poll["none_label"])
    if selected == "none":
        none_text = "✅ " + none_text
    rows.append([InlineKeyboardButton(text=none_text, callback_data=f"poll_vote:{poll_id}:none")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_polls_keyboard(polls) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for poll in polls:
        icon = "🟢" if poll["status"] == "active" else "⚫"
        question = str(poll["question"]).replace("\n", " ")
        if len(question) > 42:
            question = question[:41] + "…"
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} #{poll['id']} · {question}",
                callback_data=f"admin_poll_open:{poll['id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Обратная связь", callback_data="admin_feedback")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_poll_card_keyboard(poll_id: int, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status == "active":
        rows.append([InlineKeyboardButton(text="⛔ Завершить голосование", callback_data=f"admin_poll_close:{poll_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Голосования", callback_data="admin_polls")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
