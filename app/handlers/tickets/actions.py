import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain import OPEN_STATUSES
from app.keyboards.tickets import ticket_action_keyboard
from app.services.tickets import (
    add_ticket_comment,
    cancel_ticket_auto_close,
    get_ticket_by_id,
    take_ticket,
    update_ticket_status,
)
from app.states import TicketActionStates
from app.services.ui_messages import delete_trigger_message, is_primary_ui_message, send_ui_text
from app.services.ui_versions import pc_ticket_workspace_enabled
from app.utils import html_escape

from .utils import (
    can_creator_control_ticket,
    can_participant_cancel_ticket,
    can_user_comment_ticket,
    can_user_resolve_ticket,
    can_user_return_ticket,
    can_user_take_ticket,
    can_user_view_ticket,
    department_by_role,
    get_current_user_and_admin,
    is_client_to_purchasing_ticket,
    notify_department_about_ticket,
    notify_ticket_creator,
    row_get,
)
from .views import send_completed_ticket_card_to_creator, send_ticket_card
from .workspace import show_workspace_ticket

router = Router()


async def _is_workspace_call(call: CallbackQuery) -> bool:
    return bool(
        pc_ticket_workspace_enabled()
        and await is_primary_ui_message(call.from_user.id, getattr(call.message, "message_id", None))
    )


@router.callback_query(F.data.startswith("ticket_open:"))
async def open_ticket_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)

    if not ticket:
        await call.answer("Тикет не найден.", show_alert=True)
        return

    if not can_user_view_ticket(ticket, user, admin_flag):
        await call.answer("Нет доступа к этому тикету.", show_alert=True)
        return

    if await _is_workspace_call(call):
        await show_workspace_ticket(call, ticket_id)
        return

    await send_ticket_card(call, ticket, user, admin_flag)

@router.callback_query(F.data.startswith("ticket_take:"))
async def take_ticket_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)

    if not can_user_take_ticket(ticket, user):
        await call.answer("Ты не можешь взять этот тикет в работу.", show_alert=True)
        return

    changed = await take_ticket(ticket_id, call.from_user.id)
    if not changed:
        await call.answer("Тикет уже взят в работу другим сотрудником.", show_alert=True)
        return

    updated_ticket = await get_ticket_by_id(ticket_id)

    await call.message.edit_reply_markup(reply_markup=ticket_action_keyboard(updated_ticket, user, admin_flag))

    await notify_ticket_creator(
        bot=call.bot,
        ticket=updated_ticket,
        text=f"✅ Тикет #{ticket_id} взят в работу."
    )

    await call.answer()

@router.callback_query(F.data.startswith("ticket_comment_done:"))
async def ticket_comment_done_callback(call: CallbackQuery, state: FSMContext):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)

    if not can_user_resolve_ticket(ticket, user):
        await call.answer("Ты не можешь выполнить этот тикет.", show_alert=True)
        return

    await _start_comment_entry(call, state, ticket, user, close_after_comment=True)

@router.callback_query(F.data.startswith("ticket_comment:"))
async def ticket_comment_callback(call: CallbackQuery, state: FSMContext):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)

    if not can_user_comment_ticket(ticket, user, admin_flag):
        await call.answer("Ты не можешь комментировать этот тикет.", show_alert=True)
        return

    await _start_comment_entry(call, state, ticket, user, close_after_comment=False)

async def _start_comment_entry(call: CallbackQuery, state: FSMContext, ticket, user, *, close_after_comment: bool):
    ticket_id = int(ticket["id"])
    workspace_source = await _is_workspace_call(call)
    await state.clear()
    await state.update_data(
        ticket_id=ticket_id,
        close_after_comment=close_after_comment,
        entry_source="workspace" if workspace_source else "legacy",
    )
    await state.set_state(TicketActionStates.waiting_comment)

    prompt = (
        f"✍️ <b>Ответ на тикет #{ticket_id}</b>\n\n"
        "Напиши ответ одним сообщением."
        + (" После отправки тикет будет выполнен." if close_after_comment else "")
    )
    if workspace_source:
        await send_ui_text(
            call.bot,
            chat_id=call.from_user.id,
            text=prompt,
        )
    else:
        await call.message.answer(prompt)
    await call.answer()


async def _submit_comment_text(target, state: FSMContext, comment_text: str):
    data = await state.get_data()
    ticket_id = int(data.get("ticket_id"))
    close_after_comment = bool(data.get("close_after_comment", False))
    entry_source = str(data.get("entry_source") or "legacy")
    workspace_source = entry_source == "workspace" and pc_ticket_workspace_enabled()
    user_id = target.from_user.id
    bot = target.bot
    answer = target.message.answer if isinstance(target, CallbackQuery) else target.answer

    user, admin_flag = await get_current_user_and_admin(user_id)
    ticket = await get_ticket_by_id(ticket_id)

    if not can_user_comment_ticket(ticket, user, admin_flag):
        await state.clear()
        await answer("Ты не можешь комментировать этот тикет.")
        if isinstance(target, CallbackQuery):
            await target.answer()
        return

    user_department = department_by_role(row_get(user, "role"))
    executor_department = row_get(ticket, "executor_department")
    created_by = int(row_get(ticket, "created_by", 0))
    status = row_get(ticket, "status")
    comment_text = comment_text.strip()
    safe_comment = html_escape(comment_text)
    delayed_close_markup = None
    completion_headline = None

    if close_after_comment and can_user_resolve_ticket(ticket, user):
        if is_client_to_purchasing_ticket(ticket):
            changed = await update_ticket_status(
                ticket_id,
                "done",
                actor_telegram_id=user_id,
                comment=comment_text,
                expected_statuses=("new", "in_work"),
            )
            if not changed:
                await state.clear()
                await answer("Действие уже неактуально: состояние тикета изменилось.")
                if isinstance(target, CallbackQuery):
                    await target.answer()
                return
            result_text = f"✅ Комментарий добавлен, тикет #{ticket_id} выполнен и закрыт."
            notify_text = (
                f"✅ Новый ответ в тикете #{ticket_id}.\n\n"
                f"{safe_comment}\n\n"
                "Тикет закрыт как выполненный. Если вопрос ещё актуален — верни его в работу."
            )
            completion_headline = (
                f"✅ Тикет #{ticket_id} выполнен и закрыт.\n"
                "Ниже — полная карточка тикета со всей перепиской. "
                "Если вопрос ещё актуален — нажми «Вернуть в работу»."
            )
        else:
            changed = await update_ticket_status(
                ticket_id,
                "waiting_confirmation",
                actor_telegram_id=user_id,
                comment=comment_text,
                expected_statuses=("new", "in_work"),
            )
            if not changed:
                await state.clear()
                await answer("Действие уже неактуально: состояние тикета изменилось.")
                if isinstance(target, CallbackQuery):
                    await target.answer()
                return
            result_text = f"✅ Комментарий добавлен, тикет #{ticket_id} ожидает подтверждения автора."
            notify_text = (
                f"✅ Новый ответ в тикете #{ticket_id}.\n\n"
                f"Тикет помечен выполненным. Подтверди выполнение или верни тикет в работу.\n\n"
                f"{safe_comment}"
            )
            completion_headline = (
                f"✅ Тикет #{ticket_id} помечен выполненным.\n"
                "Ниже — полная карточка тикета со всей перепиской. "
                "Подтверди выполнение или верни тикет в работу."
            )
    else:
        auto_close_cancelled = await add_ticket_comment(
            ticket_id=ticket_id,
            author_telegram_id=user_id,
            text=comment_text,
            cancel_auto_close=True,
            # Ответ переводит новый тикет в работу, но назначение остаётся добровольным.
            start_work_if_new=(status == "new" and user_department == executor_department),
        )
        result_text = f"✅ Комментарий добавлен к тикету #{ticket_id}."
        notify_text = f"💬 Новый комментарий в тикете #{ticket_id}\n\n{safe_comment}"
        if auto_close_cancelled:
            result_text += " Автоматическое закрытие отменено, тикет возвращён в работу."
            notify_text += "\n\n↩️ Автоматическое закрытие отменено, тикет снова в работе."

    await state.clear()
    updated_ticket = await get_ticket_by_id(ticket_id)

    if user_id == created_by:
        await notify_department_about_ticket(
            bot=bot,
            department=executor_department,
            text=notify_text,
            exclude_telegram_id=user_id,
            ticket_id=ticket_id,
            use_ticket_actions=False,
        )
    else:
        if completion_headline:
            await send_completed_ticket_card_to_creator(
                bot=bot,
                ticket=updated_ticket,
                headline=completion_headline,
            )
        else:
            await notify_ticket_creator(
                bot=bot,
                ticket=updated_ticket,
                text=notify_text,
                reply_markup=delayed_close_markup,
            )

    if workspace_source:
        # Технический текст пользователя нужен только как ввод. После успешного
        # сохранения убираем его и возвращаем primary UI к актуальной карточке.
        if isinstance(target, Message):
            await delete_trigger_message(target)
        await show_workspace_ticket(target, ticket_id, answer_callback=False)
    else:
        await answer(result_text)

    if isinstance(target, CallbackQuery):
        await target.answer()


@router.message(TicketActionStates.waiting_comment)
async def process_ticket_comment(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Комментарий должен быть текстом.")
        return
    await _submit_comment_text(message, state, message.text)


@router.callback_query(F.data.startswith("ticket_tpl"))
async def disabled_template_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Шаблоны ответов отключены.", show_alert=True)
    await call.message.answer("Шаблоны ответов больше не используются. Открой тикет и нажми «Ответить», затем напиши текст своими словами.")


@router.callback_query(F.data.startswith("ticket_resolve:"))
async def ticket_resolve_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    workspace_source = await _is_workspace_call(call)

    if not can_user_resolve_ticket(ticket, user):
        await call.answer("Ты не можешь выполнить этот тикет.", show_alert=True)
        return

    if is_client_to_purchasing_ticket(ticket):
        changed = await update_ticket_status(
            ticket_id,
            "done",
            actor_telegram_id=call.from_user.id,
            comment=None,
            expected_statuses=("new", "in_work"),
        )
        if not changed:
            await call.answer("Действие уже неактуально: состояние тикета изменилось.", show_alert=True)
            return

        updated_ticket = await get_ticket_by_id(ticket_id)

        await send_completed_ticket_card_to_creator(
            bot=call.bot,
            ticket=updated_ticket,
            headline=(
                f"✅ Тикет #{ticket_id} выполнен и закрыт.\n"
                "Ниже — полная карточка тикета со всей перепиской. "
                "Если вопрос ещё актуален — нажми «Вернуть в работу»."
            ),
        )
    else:
        changed = await update_ticket_status(
            ticket_id,
            "waiting_confirmation",
            actor_telegram_id=call.from_user.id,
            comment=None,
            expected_statuses=("new", "in_work"),
        )
        if not changed:
            await call.answer("Действие уже неактуально: состояние тикета изменилось.", show_alert=True)
            return

        updated_ticket = await get_ticket_by_id(ticket_id)

        await send_completed_ticket_card_to_creator(
            bot=call.bot,
            ticket=updated_ticket,
            headline=(
                f"✅ Тикет #{ticket_id} помечен выполненным.\n"
                "Ниже — полная карточка тикета со всей перепиской. "
                "Подтверди выполнение или верни тикет в работу."
            ),
        )

    if workspace_source:
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
    else:
        await call.message.edit_reply_markup(
            reply_markup=ticket_action_keyboard(updated_ticket, user, admin_flag)
        )
    await call.answer()

@router.callback_query(F.data.startswith("ticket_continue_auto_close:"))
async def ticket_continue_auto_close_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    workspace_source = await _is_workspace_call(call)

    if not can_creator_control_ticket(ticket, user):
        await call.answer("Только автор тикета может продолжить обсуждение.", show_alert=True)
        return

    if (
        row_get(ticket, "status") != "waiting_confirmation"
        or not row_get(ticket, "auto_close_at")
    ):
        await call.answer("Это действие уже неактуально.", show_alert=True)
        return

    changed = await cancel_ticket_auto_close(
        ticket_id,
        call.from_user.id,
        "Автор продолжил обсуждение",
        comment="Автор продолжил обсуждение. Автоматическое закрытие отменено.",
    )
    if not changed:
        await call.answer("Это действие уже неактуально.", show_alert=True)
        return

    updated_ticket = await get_ticket_by_id(ticket_id)
    if workspace_source:
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
    else:
        await call.message.edit_reply_markup(
            reply_markup=ticket_action_keyboard(updated_ticket, user, admin_flag)
        )

    await notify_department_about_ticket(
        bot=call.bot,
        department=row_get(updated_ticket, "executor_department"),
        text=f"↩️ Автор продолжил обсуждение тикета #{ticket_id}. Тикет снова в работе.",
        exclude_telegram_id=call.from_user.id,
        ticket_id=ticket_id,
        use_ticket_actions=False,
    )
    await call.answer("Автоматическое закрытие отменено. Тикет возвращён в работу.")


@router.callback_query(F.data.startswith("ticket_close_now:"))
async def ticket_close_now_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    workspace_source = await _is_workspace_call(call)

    if not can_creator_control_ticket(ticket, user):
        await call.answer("Только автор тикета может закрыть его сейчас.", show_alert=True)
        return

    if (
        row_get(ticket, "status") != "waiting_confirmation"
        or not row_get(ticket, "auto_close_at")
    ):
        await call.answer("Это действие уже неактуально.", show_alert=True)
        return

    changed = await update_ticket_status(
        ticket_id,
        "done",
        actor_telegram_id=call.from_user.id,
        comment="Автор закрыл выполненный тикет без ожидания автоматического закрытия.",
        expected_statuses=("waiting_confirmation",),
        require_auto_close=True,
    )
    if not changed:
        await call.answer("Это действие уже неактуально.", show_alert=True)
        return

    updated_ticket = await get_ticket_by_id(ticket_id)
    if workspace_source:
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
    else:
        await call.message.edit_reply_markup(
            reply_markup=ticket_action_keyboard(updated_ticket, user, admin_flag)
        )

    await notify_department_about_ticket(
        bot=call.bot,
        department=row_get(updated_ticket, "executor_department"),
        text=f"✅ Тикет #{ticket_id} закрыт автором как выполненный.",
        exclude_telegram_id=call.from_user.id,
        ticket_id=ticket_id,
        use_ticket_actions=False,
    )
    await call.answer("Тикет закрыт.")


@router.callback_query(F.data.startswith("ticket_confirm_close:"))
async def ticket_confirm_close_callback(call: CallbackQuery):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    workspace_source = await _is_workspace_call(call)

    if not can_creator_control_ticket(ticket, user):
        await call.answer("Только автор тикета может подтвердить выполнение.", show_alert=True)
        return

    if row_get(ticket, "status") != "waiting_confirmation":
        await call.answer("Этот тикет сейчас не ожидает подтверждения.", show_alert=True)
        return

    changed = await update_ticket_status(
        ticket_id,
        "done",
        actor_telegram_id=call.from_user.id,
        comment="Автор подтвердил выполнение. Тикет закрыт.",
        expected_statuses=("waiting_confirmation",),
        require_auto_close=False,
    )
    if not changed:
        await call.answer("Это действие уже неактуально.", show_alert=True)
        return

    updated_ticket = await get_ticket_by_id(ticket_id)

    if workspace_source:
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
    else:
        await call.message.edit_reply_markup(reply_markup=ticket_action_keyboard(updated_ticket, user, admin_flag))

    await notify_department_about_ticket(
        bot=call.bot,
        department=row_get(updated_ticket, "executor_department"),
        text=f"✅ Тикет #{ticket_id} закрыт автором.",
        exclude_telegram_id=call.from_user.id,
        ticket_id=ticket_id,
        use_ticket_actions=False,
    )

    await call.answer()

@router.callback_query(F.data.startswith("ticket_return:"))
async def ticket_return_callback(call: CallbackQuery, state: FSMContext):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    workspace_source = await _is_workspace_call(call)

    if not can_user_return_ticket(ticket, user, admin_flag):
        await call.answer("Ты не можешь вернуть этот тикет в работу.", show_alert=True)
        return

    await state.clear()
    changed = await update_ticket_status(
        ticket_id,
        "in_work",
        actor_telegram_id=call.from_user.id,
        comment="Тикет возвращён в работу клиентским отделом.",
        expected_statuses=("waiting_confirmation", "done", "cancelled"),
        require_auto_close=False,
    )
    if not changed:
        await call.answer("Это действие уже неактуально.", show_alert=True)
        return

    updated_ticket = await get_ticket_by_id(ticket_id)

    if workspace_source:
        await show_workspace_ticket(call, ticket_id, answer_callback=False)
    else:
        await call.message.edit_reply_markup(reply_markup=ticket_action_keyboard(updated_ticket, user, admin_flag))

    await notify_department_about_ticket(
        bot=call.bot,
        department=row_get(updated_ticket, "executor_department"),
        text=f"↩️ Тикет #{ticket_id} возвращён в работу клиентским отделом.",
        exclude_telegram_id=call.from_user.id,
        ticket_id=ticket_id,
        use_ticket_actions=False,
    )

    await call.answer()

@router.message(TicketActionStates.waiting_return_reason)
async def process_return_reason(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Причина возврата больше не требуется. Открой тикет и нажми “Вернуть в работу”.")

@router.callback_query(F.data.startswith("ticket_cancel:"))
async def ticket_cancel_callback(call: CallbackQuery, state: FSMContext):
    ticket_id = int(call.data.split(":")[1])

    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)
    workspace_source = await _is_workspace_call(call)

    if not can_participant_cancel_ticket(ticket, user, admin_flag):
        await call.answer("Закрыть тикет как неактуальный может его автор или администратор.", show_alert=True)
        return

    await state.clear()
    await state.set_state(TicketActionStates.waiting_cancel_reason)
    await state.update_data(ticket_id=ticket_id, entry_source="workspace" if workspace_source else "legacy")

    prompt = f"❌ <b>Закрытие тикета #{ticket_id}</b>\n\nНапиши причину закрытия/отмены одним сообщением."
    if workspace_source:
        await send_ui_text(call.bot, chat_id=call.from_user.id, text=prompt)
    else:
        await call.message.answer(prompt)
    await call.answer()

@router.message(TicketActionStates.waiting_cancel_reason)
async def process_cancel_reason(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Причина должна быть текстом.")
        return

    data = await state.get_data()
    ticket_id = int(data.get("ticket_id"))
    workspace_source = str(data.get("entry_source") or "legacy") == "workspace" and pc_ticket_workspace_enabled()

    user, admin_flag = await get_current_user_and_admin(message.from_user.id)
    ticket = await get_ticket_by_id(ticket_id)

    if not can_participant_cancel_ticket(ticket, user, admin_flag):
        await state.clear()
        await message.answer("Закрыть этот тикет как неактуальный может только его автор или администратор.")
        return

    reason_text = message.text.strip()
    changed = await update_ticket_status(
        ticket_id,
        "cancelled",
        actor_telegram_id=message.from_user.id,
        comment=f"Тикет досрочно закрыт. Причина: {reason_text}",
        expected_statuses=OPEN_STATUSES,
    )
    if not changed:
        await state.clear()
        await message.answer("Действие уже неактуально: тикет уже закрыт или изменён.")
        return

    await state.clear()

    updated_ticket = await get_ticket_by_id(ticket_id)

    actor_department = department_by_role(row_get(user, "role"))
    target_department = (
        row_get(updated_ticket, "requester_department")
        if actor_department == row_get(updated_ticket, "executor_department")
        else row_get(updated_ticket, "executor_department")
    )
    await notify_department_about_ticket(
        bot=message.bot,
        department=target_department,
        text=f"❌ Тикет #{ticket_id} досрочно закрыт.\n\nПричина: {html_escape(reason_text)}",
        exclude_telegram_id=message.from_user.id,
        ticket_id=ticket_id,
        use_ticket_actions=False,
    )
    if workspace_source:
        await delete_trigger_message(message)
        await show_workspace_ticket(message, ticket_id, answer_callback=False)
    else:
        await message.answer(f"✅ Тикет #{ticket_id} закрыт.")
