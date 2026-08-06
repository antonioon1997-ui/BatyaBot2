from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import DEPARTMENT_CLIENT, DEPARTMENT_PURCHASING, department_by_role
from app.keyboards.common import bottom_menu_for_role, main_menu_for_role
from app.keyboards.order_status import (
    order_status_cancel_keyboard,
    order_status_result_keyboard,
    order_status_unavailable_keyboard,
)
from app.keyboards.tickets import post_create_options_keyboard
from app.services.attachments import create_attachment
from app.services.order_status import (
    OrderStatusLookup,
    OrderStatusRecord,
    OrderStatusUnavailable,
    extract_order_number_from_query,
    get_order_status,
)
from app.services.ui_messages import UiMessagePart, clear_ui_message_bundle, send_ui_parts, send_ui_text
from app.services.tickets import (
    add_ticket_event,
    create_ticket,
    get_ticket_by_id,
    set_ticket_category,
)
from app.states import OrderStatusStates
from app.utils import html_escape

from .utils import (
    get_current_user_and_admin,
    notify_department_about_ticket,
    row_get,
)


router = Router()
logger = logging.getLogger(__name__)


def _plain_snapshot(record: OrderStatusRecord | None, *, stale: bool = False) -> str:
    if record is None:
        text = "Данные заказа не найдены в таблице активных заказов."
    else:
        lines = [
            f"Статус МС: {record.ms_status}",
            "",
            "Статусы заказов поставщиков:",
            *record.purchasing_items,
        ]
        text = "\n".join(lines)

    if stale:
        text += "\n\nПоказаны данные последнего успешного обновления кэша."

    return text


def _lookup_text(record: OrderStatusRecord, *, purchasing: bool, stale: bool) -> str:
    if purchasing:
        heading = "Статусы заказов поставщиков:"
        items = record.purchasing_items
    else:
        heading = "Товары в заказе:"
        items = record.client_items

    text = (
        f"🔎 <b>Заказ: {html_escape(record.order_number)}</b>\n\n"
        f"<b>Статус МС:</b> {html_escape(record.ms_status)}\n\n"
        f"<b>{heading}</b>\n"
        + "\n".join(html_escape(item) for item in items)
    )

    if stale:
        text += (
            "\n\n⚠️ <i>Google Таблица временно недоступна. "
            "Показаны данные последнего успешного обновления.</i>"
        )

    return text


def _attachment_from_message(message: Message) -> dict | None:
    if message.photo:
        return {
            "file_id": message.photo[-1].file_id,
            "file_type": "photo",
            "file_name": None,
        }
    if message.document:
        return {
            "file_id": message.document.file_id,
            "file_type": "document",
            "file_name": message.document.file_name,
        }
    if message.video:
        return {
            "file_id": message.video.file_id,
            "file_type": "video",
            "file_name": message.video.file_name,
        }
    return None


async def _active_user(target):
    user, admin_flag = await get_current_user_and_admin(target.from_user.id)
    if not user or int(row_get(user, "is_active", 0) or 0) != 1:
        return None, admin_flag, None
    return user, admin_flag, department_by_role(row_get(user, "role"))


async def _send_main_menu(target, state: FSMContext) -> None:
    await state.clear()
    user, admin_flag, _ = await _active_user(target)
    if not user:
        if isinstance(target, CallbackQuery):
            await target.answer("Нет доступа.", show_alert=True)
        else:
            await target.answer("Нет доступа.")
        return

    await send_ui_parts(
        target.bot,
        chat_id=target.from_user.id,
        parts=[
            UiMessagePart(
                "Главное меню.",
                bottom_menu_for_role(row_get(user, "role"), is_admin=admin_flag),
            ),
            UiMessagePart(
                "Выбери действие:",
                main_menu_for_role(row_get(user, "role"), is_admin=admin_flag),
            ),
        ],
    )
    if isinstance(target, CallbackQuery):
        await target.answer()


async def _start_lookup(target, state: FSMContext) -> None:
    user, _, department = await _active_user(target)
    if not user or department not in {DEPARTMENT_CLIENT, DEPARTMENT_PURCHASING}:
        text = "Проверка статуса доступна сотрудникам клиентского отдела и отдела закупки."
        if isinstance(target, CallbackQuery):
            await target.answer(text, show_alert=True)
        else:
            await target.answer(text)
        return

    await state.clear()
    await state.set_state(OrderStatusStates.waiting_order_number)
    await send_ui_text(
        target.bot,
        chat_id=target.from_user.id,
        text=(
            "🔎 <b>Узнать статус заказа</b>\n\n"
            "Введи номер заказа МойСклад.\n\n"
            "Можно отправить, например: <code>11786</code>, <code>Заказ 11786</code> или <code>№11786</code>."
        ),
        reply_markup=order_status_cancel_keyboard(),
    )
    if isinstance(target, CallbackQuery):
        await target.answer()


async def _lookup_and_send(message: Message, state: FSMContext, order_number: str) -> None:
    user, _, department = await _active_user(message)
    if not user or department not in {DEPARTMENT_CLIENT, DEPARTMENT_PURCHASING}:
        await state.clear()
        await message.answer("Нет доступа.")
        return

    try:
        lookup = await get_order_status(order_number)
    except OrderStatusUnavailable as exc:
        logger.warning("Статусы заказов временно недоступны: %s", exc)
        await state.clear()
        await send_ui_text(
            message.bot,
            chat_id=message.from_user.id,
            text=(
                "⚠️ Не удалось получить данные из Google Таблицы.\n\n"
                "Работа тикетов не нарушена. Повтори запрос через несколько секунд."
            ),
            reply_markup=order_status_unavailable_keyboard(),
        )
        return

    await state.clear()
    allow_question = department == DEPARTMENT_CLIENT

    if lookup.record is None:
        if lookup.stale:
            text = (
                f"⚠️ Заказ <b>{html_escape(order_number)}</b> не найден в последней сохранённой копии данных.\n\n"
                "Свежую таблицу сейчас получить не удалось, поэтому результат может быть устаревшим."
            )
        else:
            text = (
                f"Заказ <b>{html_escape(order_number)}</b> не найден в таблице активных заказов.\n\n"
                "Проверь номер. Возможно, заказ уже закрыт и отсутствует в списке активных заказов."
            )
    else:
        text = _lookup_text(
            lookup.record,
            purchasing=department == DEPARTMENT_PURCHASING,
            stale=lookup.stale,
        )

    await send_ui_text(
        message.bot,
        chat_id=message.from_user.id,
        text=text,
        reply_markup=order_status_result_keyboard(
            order_number,
            allow_question=allow_question,
        ),
    )


@router.message(F.text == "🔎 Узнать статус заказа")
async def bottom_order_status(message: Message, state: FSMContext):
    await _start_lookup(message, state)


@router.callback_query(F.data == "order_status_start")
async def callback_order_status_start(call: CallbackQuery, state: FSMContext):
    await _start_lookup(call, state)


@router.callback_query(F.data == "order_status_cancel")
async def callback_order_status_cancel(call: CallbackQuery, state: FSMContext):
    await _send_main_menu(call, state)


@router.message(OrderStatusStates.waiting_order_number)
async def process_order_number(message: Message, state: FSMContext):
    order_number = extract_order_number_from_query(message.text or message.caption)
    if not order_number:
        await send_ui_text(
            message.bot,
            chat_id=message.from_user.id,
            text="Не удалось распознать номер заказа. Отправь только номер, например <code>11786</code>.",
            reply_markup=order_status_cancel_keyboard(),
        )
        return

    await _lookup_and_send(message, state, order_number)


@router.callback_query(F.data.startswith("order_status_ask:"))
async def callback_order_status_ask(call: CallbackQuery, state: FSMContext):
    user, _, department = await _active_user(call)
    if not user:
        await call.answer("Нет доступа.", show_alert=True)
        return
    if department != DEPARTMENT_CLIENT:
        await call.answer(
            "Создание вопроса по этой кнопке предназначено для клиентского отдела.",
            show_alert=True,
        )
        return

    order_number = extract_order_number_from_query(call.data.split(":", 1)[1])
    if not order_number:
        await call.answer("Номер заказа в кнопке повреждён.", show_alert=True)
        return

    lookup: OrderStatusLookup | None = None
    try:
        lookup = await get_order_status(order_number)
    except OrderStatusUnavailable:
        # Вопрос всё равно можно создать: отсутствие Google не должно блокировать тикеты.
        lookup = None

    snapshot = _plain_snapshot(
        lookup.record if lookup else None,
        stale=bool(lookup and lookup.stale),
    )

    await state.clear()
    await state.set_state(OrderStatusStates.waiting_question)
    await state.update_data(
        order_status_order_number=order_number,
        order_status_snapshot=snapshot,
    )
    await send_ui_text(
        call.bot,
        chat_id=call.from_user.id,
        text=(
            f"❓ <b>Вопрос по заказу {html_escape(order_number)}</b>\n\n"
            "Напиши вопрос одним сообщением. Можно отправить фото, документ или видео с подписью."
        ),
        reply_markup=order_status_cancel_keyboard(),
    )
    await call.answer()


@router.message(OrderStatusStates.waiting_question)
async def process_order_question(message: Message, state: FSMContext):
    user, _, department = await _active_user(message)
    if not user or department != DEPARTMENT_CLIENT:
        await state.clear()
        await message.answer("Создать такой вопрос может только сотрудник клиентского отдела.")
        return

    question = (message.text or message.caption or "").strip()
    if not question:
        await send_ui_text(
            message.bot,
            chat_id=message.from_user.id,
            text="Добавь текст вопроса. Если отправляешь вложение, напиши вопрос в подписи.",
            reply_markup=order_status_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    order_number = extract_order_number_from_query(data.get("order_status_order_number"))
    snapshot = str(data.get("order_status_snapshot") or "").strip()
    if not order_number:
        await state.clear()
        await message.answer("Данные создания устарели. Проверь заказ заново.")
        return

    title_question = re.sub(r"\s+", " ", question).strip()
    title = f"Вопрос по заказу {order_number}: {title_question}"[:250]
    attachment = _attachment_from_message(message)

    try:
        ticket_id = await create_ticket(
            title=title,
            description=question,
            order_number=order_number,
            created_by=message.from_user.id,
            requester_department=DEPARTMENT_CLIENT,
            executor_department=DEPARTMENT_PURCHASING,
            ticket_type="task",
            order_status_snapshot=snapshot or None,
        )
        await set_ticket_category(ticket_id, "question", message.from_user.id)

        if attachment:
            await create_attachment(
                ticket_id=ticket_id,
                file_id=attachment["file_id"],
                file_type=attachment["file_type"],
                file_name=attachment.get("file_name"),
                uploaded_by=message.from_user.id,
            )

        await add_ticket_event(
            ticket_id,
            "order_status_attached",
            actor_telegram_id=message.from_user.id,
            details="К тикету приложен снимок статуса заказа из Google Sheets",
        )
    except Exception:
        logger.exception("Не удалось создать вопрос по заказу %s", order_number)
        await send_ui_text(
            message.bot,
            chat_id=message.from_user.id,
            text="Не удалось создать тикет из-за внутренней ошибки. Данные не потеряны — попробуй отправить вопрос ещё раз.",
            reply_markup=order_status_cancel_keyboard(),
        )
        return

    await state.clear()
    await clear_ui_message_bundle(message.bot, chat_id=message.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    await message.answer(
        f"✅ Тикет #{ticket_id} по заказу {html_escape(order_number)} создан и отправлен в отдел закупки.\n\n"
        "Статус заказа и номера заказов поставщиков приложены к тикету.",
        reply_markup=post_create_options_keyboard(
            ticket_id,
            row_get(ticket, "priority", "normal"),
            row_get(ticket, "category"),
        ),
    )

    attachment_line = "📎 Есть вложение: 1 шт.\n" if attachment else ""
    author_name = row_get(user, "full_name") or row_get(user, "username") or message.from_user.id
    snapshot_block = snapshot or "Данные заказа не найдены в таблице активных заказов."
    await notify_department_about_ticket(
        bot=message.bot,
        department=DEPARTMENT_PURCHASING,
        text=(
            f"🆕 <b>Новый вопрос по заказу — тикет #{ticket_id}</b>\n\n"
            f"🔢 <b>Заказ:</b> {html_escape(order_number)}\n"
            f"{attachment_line}\n"
            f"📦 <b>Статус заказа на момент создания:</b>\n{html_escape(snapshot_block)}\n\n"
            f"👤 <b>Автор:</b> {html_escape(author_name)}\n"
            f"📝 <b>Вопрос:</b>\n{html_escape(question)}"
        ),
        exclude_telegram_id=message.from_user.id,
        ticket_id=ticket_id,
        use_ticket_actions=True,
    )
