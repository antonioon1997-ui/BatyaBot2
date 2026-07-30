import logging
import re

from aiogram.types import CallbackQuery

from app.domain import (
    CLOSED_STATUSES,
    DEPARTMENT_CLIENT,
    DEPARTMENT_PURCHASING,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_IN_WORK,
    STATUS_NEW,
    STATUS_WAITING_CONFIRMATION,
    department_by_role,
    is_observer_role,
)
from app.keyboards.common import main_menu_for_role
from app.keyboards.tickets import open_ticket_keyboard, ticket_action_keyboard
from app.services.tickets import get_active_users_by_department, get_ticket_by_id
from app.services.users import get_user_by_telegram_id, is_admin
from app.utils import html_escape

logger = logging.getLogger(__name__)

STATUS_NAMES = {"new":"🆕 Новый","in_work":"🛠 В работе","waiting_answer":"⏳ Ожидает ответа","waiting_confirmation":"✅ Ожидает подтверждения","done":"🏁 Закрыт","cancelled":"❌ Отменён"}
DEPARTMENT_NAMES = {"purchasing":"Отдел закупки","client":"Клиентский отдел","unknown":"Не определён"}
TELEGRAM_TEXT_LIMIT = 4096
SAFE_TEXT_LIMIT = 3800

def get_status_name(status: str | None) -> str:
    if not status:
        return "Неизвестно"

    return STATUS_NAMES.get(status, html_escape(status))

def get_department_name(department: str | None) -> str:
    if not department:
        return "Не определён"

    return DEPARTMENT_NAMES.get(department, html_escape(department))

def is_closed_status(status: str | None) -> bool:
    return status in CLOSED_STATUSES

def text_or_dash(value) -> str:
    if value is None:
        return "—"

    value = str(value).strip()

    if not value:
        return "—"

    return html_escape(value)

def has_text_value(value) -> bool:
    if value is None:
        return False

    return bool(str(value).strip())

def optional_line(prefix: str, value) -> str:
    if not has_text_value(value):
        return ""

    return f"{prefix}{html_escape(str(value).strip())}\n"

def short_text(value, limit: int = 700) -> str:
    if value is None or not str(value).strip():
        return "—"

    raw = str(value).strip()
    if len(raw) > limit:
        raw = raw[:limit].rstrip() + "..."
    return html_escape(raw)

def row_get(row, key, default=None):
    if row is None:
        return default

    try:
        if key in row.keys():
            return row[key]
    except Exception:
        return default

    return default

def split_long_text(text: str, limit: int = SAFE_TEXT_LIMIT) -> list[str]:
    if text is None:
        return [""]

    text = str(text)

    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""

    for paragraph in text.split("\n"):
        add = paragraph if not current else "\n" + paragraph

        if len(current) + len(add) <= limit:
            current += add
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph) <= limit:
            current = paragraph
        else:
            start = 0

            while start < len(paragraph):
                chunks.append(paragraph[start:start + limit])
                start += limit

    if current:
        chunks.append(current)

    return chunks

async def answer_long(message_or_call, text: str, reply_markup=None):
    chunks = split_long_text(text)
    total = len(chunks)

    target_message = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call

    for index, chunk in enumerate(chunks, start=1):
        prefix = ""

        if total > 1:
            prefix = f"Часть {index}/{total}\n\n"

        markup = reply_markup if index == total else None

        await target_message.answer(
            prefix + chunk,
            reply_markup=markup
        )

async def send_long_to_user(bot, chat_id: int, text: str, reply_markup=None):
    chunks = split_long_text(text)
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        prefix = ""

        if total > 1:
            prefix = f"Часть {index}/{total}\n\n"

        markup = reply_markup if index == total else None

        await bot.send_message(
            chat_id=chat_id,
            text=prefix + chunk,
            reply_markup=markup
        )

def get_author_name_from_ticket(ticket) -> str:
    full_name = row_get(ticket, "creator_full_name")
    username = row_get(ticket, "creator_username")
    created_by = row_get(ticket, "created_by")

    if has_text_value(full_name):
        return html_escape(str(full_name).strip())

    if has_text_value(username):
        username = str(username).strip()
        if username.startswith("@"):
            return html_escape(username)
        return f"@{html_escape(username)}"

    return html_escape(created_by)

def extract_order_number(text: str | None) -> str | None:
    """Извлекает номер заказа только из явной формулировки про заказ.

    Поддерживаемые примеры:
    - «заказ 12345»;
    - «по заказу 12345»;
    - «заказ №AB-123»;
    - «номер заказа: 12345».

    Слова вроде «заказать», «заказываем» и «заказчик» не совпадают,
    потому что после слова «заказ» требуется граница слова, а найденное
    значение обязательно должно содержать хотя бы одну цифру.
    """
    if not text:
        return None

    patterns = [
        # «номер заказа 123», «номер заказа: AB-123»
        r"(?iu)\bномер\s+заказ(?:а|у|е|ом)?\b\s*(?:№|#)?\s*[:=\-]?\s*"
        r"([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9/_\-.]{0,63})",
        # «заказ 123», «по заказу №123», «заказ N 123»
        r"(?iu)(?:\bпо\s+)?\bзаказ(?:а|у|е|ом)?\b\s*"
        r"(?:(?:№|#)|(?:n|н)\s*)?\s*[:=\-]?\s*"
        r"([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9/_\-.]{0,63})",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1).strip(".,;:!?()[]{} ")

            # Номер заказа должен содержать цифру. Это не даёт принять
            # слова «нужно», «товар» и окончания слов за номер заказа.
            if value and any(char.isdigit() for char in value):
                return value

    # Дополнительное безопасное правило: первый токен — ровно пять цифр,
    # причём номер начинается с 1. Явные формулировки выше имеют приоритет.
    stripped = text.lstrip()
    first_token = stripped.split(maxsplit=1)[0] if stripped else ""
    if re.fullmatch(r"1\d{4}", first_token):
        return first_token

    return None

async def get_current_user_and_admin(telegram_id: int):
    user = await get_user_by_telegram_id(telegram_id)
    admin_flag = await is_admin(telegram_id)

    return user, admin_flag

def can_user_view_ticket(ticket, user, admin_flag: bool = False) -> bool:
    if not ticket:
        return False

    if admin_flag:
        return True

    if not user:
        return False

    user_role = row_get(user, "role")
    user_department = department_by_role(user_role)

    if is_observer_role(user_role):
        return True

    user_id = int(row_get(user, "telegram_id", 0))
    created_by = int(row_get(ticket, "created_by", 0))
    executor_department = row_get(ticket, "executor_department")
    requester_department = row_get(ticket, "requester_department")

    if user_id == created_by:
        return True

    if user_department and user_department in {executor_department, requester_department}:
        return True

    return False

def can_user_comment_ticket(ticket, user, admin_flag: bool = False) -> bool:
    if admin_flag:
        return True

    if not ticket or not user:
        return False

    if is_closed_status(row_get(ticket, "status")):
        return False

    user_role = row_get(user, "role")

    if is_observer_role(user_role):
        return False

    user_department = department_by_role(user_role)
    user_id = int(row_get(user, "telegram_id", 0))
    created_by = int(row_get(ticket, "created_by", 0))
    executor_department = row_get(ticket, "executor_department")

    return user_id == created_by or user_department == executor_department

def can_user_take_ticket(ticket, user) -> bool:
    if not ticket or not user:
        return False

    if row_get(ticket, "status") != STATUS_NEW:
        return False

    user_role = row_get(user, "role")

    if is_observer_role(user_role):
        return False

    user_department = department_by_role(user_role)
    executor_department = row_get(ticket, "executor_department")

    return user_department == executor_department

def can_user_resolve_ticket(ticket, user) -> bool:
    if not ticket or not user:
        return False

    if row_get(ticket, "status") not in {STATUS_NEW, STATUS_IN_WORK}:
        return False

    user_role = row_get(user, "role")

    if is_observer_role(user_role):
        return False

    user_department = department_by_role(user_role)
    executor_department = row_get(ticket, "executor_department")

    return user_department == executor_department

def can_creator_control_ticket(ticket, user) -> bool:
    if not ticket or not user:
        return False

    if is_closed_status(row_get(ticket, "status")):
        return False

    user_role = row_get(user, "role")

    if is_observer_role(user_role):
        return False

    user_id = int(row_get(user, "telegram_id", 0))
    created_by = int(row_get(ticket, "created_by", 0))

    return user_id == created_by


def can_participant_cancel_ticket(ticket, user, admin_flag: bool = False) -> bool:
    """Разрешает досрочное закрытие только отделу, который создал тикет."""
    if not ticket or not user or is_closed_status(row_get(ticket, "status")):
        return False

    if is_observer_role(row_get(user, "role")):
        return False

    user_department = department_by_role(row_get(user, "role"))
    requester_department = row_get(ticket, "requester_department")

    return bool(user_department) and user_department == requester_department

def can_user_return_ticket(ticket, user, admin_flag: bool = False) -> bool:
    if admin_flag:
        return True

    if not ticket or not user:
        return False

    user_role = row_get(user, "role")

    if is_observer_role(user_role):
        return False

    user_department = department_by_role(user_role)

    if user_department != DEPARTMENT_CLIENT:
        return False

    return row_get(ticket, "status") in {STATUS_WAITING_CONFIRMATION, STATUS_DONE, STATUS_CANCELLED}

def is_client_to_purchasing_ticket(ticket) -> bool:
    return (
        row_get(ticket, "requester_department") == DEPARTMENT_CLIENT
        and row_get(ticket, "executor_department") == DEPARTMENT_PURCHASING
    )

async def notify_ticket_creator(bot, ticket, text: str, reply_markup=None):
    creator_id = int(row_get(ticket, "created_by", 0))

    if creator_id:
        try:
            await send_long_to_user(
                bot=bot,
                chat_id=creator_id,
                text=text,
                reply_markup=(
                    reply_markup
                    if reply_markup is not None
                    else open_ticket_keyboard(int(row_get(ticket, "id")))
                )
            )
        except Exception:
            logger.exception("Не удалось уведомить автора тикета %s", row_get(ticket, "id"))

async def notify_department_about_ticket(
    bot,
    department: str,
    text: str,
    exclude_telegram_id: int | None = None,
    ticket_id: int | None = None,
    use_ticket_actions: bool = False,
):
    ticket = None

    if ticket_id is not None:
        ticket = await get_ticket_by_id(ticket_id)

    users = await get_active_users_by_department(department)

    for user in users:
        telegram_id = int(user["telegram_id"])

        if exclude_telegram_id and telegram_id == exclude_telegram_id:
            continue

        user_row = user
        user_department = department_by_role(user_row["role"])

        if user_department != department:
            continue

        if use_ticket_actions and ticket:
            keyboard = ticket_action_keyboard(
                ticket=ticket,
                user=user_row,
                is_admin=False,
            )
        elif ticket:
            keyboard = open_ticket_keyboard(int(row_get(ticket, "id")))
        else:
            keyboard = main_menu_for_role(
                role=row_get(user_row, "role"),
                is_admin=False,
            )

        try:
            await send_long_to_user(
                bot=bot,
                chat_id=telegram_id,
                text=text,
                reply_markup=keyboard
            )
        except Exception:
            logger.exception("Не удалось отправить уведомление пользователю %s", telegram_id)
