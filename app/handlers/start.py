from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import settings
from app.keyboards.common import access_request_keyboard, bottom_menu_for_role, main_menu_for_role
from app.services.main_menu_dashboard import build_main_menu_text
from app.services.preferences import user_text
from app.services.ui_messages import send_ui_text
from app.services.users import create_or_update_access_request, get_user_by_telegram_id
from app.utils import html_escape


router = Router()


async def _show_hybrid_main_menu(
    message: Message,
    *,
    user,
    is_admin: bool,
    restore_reply_keyboard: bool,
) -> None:
    """Показывает главное inline-меню, сохраняя нижнюю ReplyKeyboard.

    ReplyKeyboard и InlineKeyboard нельзя прикрепить к одному сообщению, поэтому
    при /start и /menu сначала отдельным коротким сообщением восстанавливаем
    постоянную нижнюю клавиатуру, а затем создаём/редактируем основную inline-панель.
    При нажатии нижней кнопки «🏠 Меню» повторно присылать ReplyKeyboard не нужно.
    """
    telegram_id = int(message.from_user.id)
    role = user["role"]

    if restore_reply_keyboard:
        await message.answer(
            "⌨️ Быстрое меню готово. Основные действия всегда доступны внизу.",
            reply_markup=bottom_menu_for_role(role, is_admin=is_admin),
        )

    menu_text = await build_main_menu_text(telegram_id, role)
    await send_ui_text(
        message.bot,
        chat_id=telegram_id,
        text=menu_text,
        reply_markup=main_menu_for_role(role, is_admin=is_admin),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, state: FSMContext):
    telegram_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    user = await get_user_by_telegram_id(telegram_id)
    is_admin = telegram_id == settings.admin_id

    if user and user["is_active"] == 1:
        await state.clear()
        await message.answer(
            await user_text(telegram_id, "access_confirmed"),
            reply_markup=bottom_menu_for_role(user["role"], is_admin=is_admin),
        )
        menu_text = await build_main_menu_text(telegram_id, user["role"])
        await send_ui_text(
            message.bot,
            chat_id=telegram_id,
            text=menu_text,
            reply_markup=main_menu_for_role(user["role"], is_admin=is_admin),
        )
        return

    request = await create_or_update_access_request(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
    )

    if is_admin:
        await message.answer(
            "Антон, ты определён как администратор по ADMIN_ID, но пользователя с активной ролью в базе ещё нет.\n\n"
            "Я отправил заявку на доступ. Одобри себя кнопкой в сообщении ниже или добавь себя в базу.\n\n"
            f"Твой Telegram ID: <code>{telegram_id}</code>"
        )
    else:
        await message.answer(
            "Доступ к боту пока не выдан.\n\n"
            f"Твой Telegram ID: <code>{telegram_id}</code>\n\n"
            "Заявка отправлена администратору. После подтверждения нажми /start ещё раз."
        )

    await bot.send_message(
        chat_id=settings.admin_id,
        text=(
            "🆕 <b>Новый пользователь запросил доступ</b>\n\n"
            f"Имя: {html_escape(full_name)}\n"
            f"Username: @{html_escape(username, default='нет')}\n"
            f"Telegram ID: <code>{telegram_id}</code>\n"
            f"Номер заявки: {request['id']}"
        ),
        reply_markup=access_request_keyboard(telegram_id),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    user = await get_user_by_telegram_id(message.from_user.id)
    if not user or int(user["is_active"] or 0) != 1:
        await message.answer("Нет доступа. Нажмите /start, чтобы запросить доступ.")
        return

    await state.clear()
    await _show_hybrid_main_menu(
        message,
        user=user,
        is_admin=int(message.from_user.id) == int(settings.admin_id),
        restore_reply_keyboard=True,
    )


@router.message(F.text == "🏠 Меню")
async def bottom_main_menu(message: Message, state: FSMContext):
    user = await get_user_by_telegram_id(message.from_user.id)
    if not user or int(user["is_active"] or 0) != 1:
        await message.answer("Нет доступа.")
        return

    await state.clear()
    await _show_hybrid_main_menu(
        message,
        user=user,
        is_admin=int(message.from_user.id) == int(settings.admin_id),
        restore_reply_keyboard=False,
    )

# В 2.6.1 основные кнопки постоянной ReplyKeyboard обрабатываются в раннем
# router'е. Это делает быстрый пульт независимым от текущего FSM-состояния:
# например, «Создать тикет» не должен восприниматься как номер заказа, если
# пользователь до этого открыл поиск статуса.
@router.message(F.text == "➕ Создать тикет")
async def quick_create_ticket(message: Message, state: FSMContext):
    from app.handlers.tickets.creation import start_create_ticket

    await start_create_ticket(message, state)


@router.message(F.text == "🔎 Узнать статус заказа")
async def quick_order_status(message: Message, state: FSMContext):
    from app.handlers.tickets.order_status import _start_lookup

    await _start_lookup(message, state)


@router.message(F.text == "❓ Помощь")
async def quick_help(message: Message, state: FSMContext):
    from app.handlers.help import _send_help

    await _send_help(message, state)


@router.message(F.text == "⚙️ Админка")
async def quick_admin(message: Message, state: FSMContext):
    from app.handlers.admin import send_admin_menu

    await state.clear()
    await send_admin_menu(message)
