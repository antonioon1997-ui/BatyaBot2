from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.services.preferences import user_text

from .utils import get_current_user_and_admin
from .views import show_main_menu

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
    user, admin_flag = await get_current_user_and_admin(message.from_user.id)

    if not user:
        await message.answer(
            "Привет! Заявка на доступ должна быть обработана администратором."
        )
        return

    await show_main_menu(message, user, admin_flag)

@router.callback_query(F.data == "main_menu")
async def callback_main_menu(call: CallbackQuery):
    user, admin_flag = await get_current_user_and_admin(call.from_user.id)
    await show_main_menu(call, user, admin_flag)

@router.message()
async def unknown_message(message: Message):
    await message.answer(await user_text(message.from_user.id, "unknown_command"))
