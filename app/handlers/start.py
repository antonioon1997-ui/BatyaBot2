from aiogram import Router, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.config import settings
from app.keyboards.common import access_request_keyboard, bottom_menu_for_role, main_menu_for_role
from app.services.preferences import user_text
from app.services.users import create_or_update_access_request, get_user_by_telegram_id
from app.utils import html_escape


router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    telegram_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    user = await get_user_by_telegram_id(telegram_id)

    is_admin = telegram_id == settings.admin_id

    if user and user["is_active"] == 1:
        role = user["role"]

        await message.answer(
            await user_text(telegram_id, "access_confirmed"),
            reply_markup=bottom_menu_for_role(role, is_admin=is_admin)
        )

        await message.answer(
            await user_text(telegram_id, "main_menu_title"),
            reply_markup=main_menu_for_role(role, is_admin=is_admin)
        )
        return

    request = await create_or_update_access_request(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name
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
        reply_markup=access_request_keyboard(telegram_id)
    )