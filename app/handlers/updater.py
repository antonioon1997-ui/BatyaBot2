from __future__ import annotations

import logging
import shutil
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.keyboards.admin import admin_menu, update_confirm_keyboard
from app.services.update_manager import (
    INCOMING_DIR,
    ensure_update_directories,
    inspect_update_archive,
    start_external_updater,
    write_pending_job,
)
from app.services.users import get_active_users, is_admin
from app.states import BotUpdateStates
from app.utils import html_escape

router = Router()
logger = logging.getLogger(__name__)


def _inspection_text(inspection) -> str:
    notes = "\n".join(f"• {html_escape(item)}" for item in inspection.release_notes)
    lines = [
        "✅ <b>Архив прошёл предварительную проверку</b>",
        "",
        f"Новых файлов: <b>{len(inspection.new_files)}</b>",
        f"Изменённых файлов: <b>{len(inspection.changed_files)}</b>",
        f"Неизменённых файлов: <b>{len(inspection.unchanged_files)}</b>",
        f"Файлов для удаления: <b>{len(inspection.delete_files)}</b>",
        "",
        "<b>Описание обновления:</b>",
        notes,
        "",
        "Защищённые данные (.env, bot.db, venv, backups, logs) не будут затронуты.",
        "Установить обновление?",
    ]
    return "\n".join(lines)


async def _begin_update(message: Message, state: FSMContext) -> None:
    if not await is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await state.clear()
    await state.set_state(BotUpdateStates.waiting_archive)
    await message.answer(
        "📦 <b>Обновление бота</b>\n\n"
        "Отправьте ZIP-архив как документ.\n\n"
        "В корне архива обязательно должен быть файл <code>update_manifest.json</code> "
        "с описанием изменений. Архив не должен содержать .env, bot.db, venv, backups, logs, "
        "исполняемые файлы или пути за пределами проекта.\n\n"
        "Для отмены нажмите кнопку админки или отправьте /cancel."
    )


@router.message(Command("update"))
async def update_command(message: Message, state: FSMContext):
    await _begin_update(message, state)


@router.callback_query(F.data == "admin_bot_update")
async def update_menu_callback(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await state.set_state(BotUpdateStates.waiting_archive)
    await call.message.answer(
        "📦 Отправьте ZIP-архив обновления как документ.\n\n"
        "В корне архива должен находиться <code>update_manifest.json</code>."
    )


@router.message(Command("cancel"))
async def cancel_update(message: Message, state: FSMContext):
    current = await state.get_state()
    if current and current.startswith(BotUpdateStates.__name__):
        data = await state.get_data()
        stage = data.get("staging_path")
        archive = data.get("archive_path")
        if stage:
            shutil.rmtree(stage, ignore_errors=True)
        if archive:
            Path(archive).unlink(missing_ok=True)
        await state.clear()
        await message.answer("Обновление отменено.", reply_markup=admin_menu())


@router.message(BotUpdateStates.waiting_archive, F.document)
async def receive_update_archive(message: Message, state: FSMContext, bot: Bot):
    if not await is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    document = message.document
    filename = document.file_name or "update.zip"
    if not filename.lower().endswith(".zip"):
        await message.answer("❌ Нужен файл с расширением .zip. Отправьте другой архив.")
        return
    if document.file_size and document.file_size > 30 * 1024 * 1024:
        await message.answer("❌ Архив больше 30 МБ. Уменьшите его и повторите загрузку.")
        return

    ensure_update_directories()
    archive_path = INCOMING_DIR / f"update_{message.from_user.id}_{message.message_id}.zip"
    await message.answer("⏳ Архив загружен. Проверяю структуру и безопасность...")
    try:
        await bot.download(document, destination=archive_path)
        inspection = inspect_update_archive(archive_path)
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        logger.exception("Архив обновления не прошёл проверку")
        await message.answer(
            "❌ <b>Обновление отклонено</b>\n\n"
            f"{html_escape(str(exc)[:3500])}\n\n"
            "Исправьте архив и отправьте его повторно."
        )
        return

    await state.update_data(
        archive_path=str(inspection.archive_path),
        staging_path=str(inspection.staging_path),
    )
    await state.set_state(BotUpdateStates.waiting_confirmation)
    await message.answer(_inspection_text(inspection), reply_markup=update_confirm_keyboard())


@router.message(BotUpdateStates.waiting_archive)
async def receive_non_archive(message: Message):
    await message.answer("Отправьте ZIP-архив как документ или используйте /cancel.")


@router.callback_query(BotUpdateStates.waiting_confirmation, F.data == "bot_update_cancel")
async def cancel_update_callback(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    shutil.rmtree(data.get("staging_path", ""), ignore_errors=True)
    archive = data.get("archive_path")
    if archive:
        Path(archive).unlink(missing_ok=True)
    await state.clear()
    await call.answer("Обновление отменено")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Обновление отменено.", reply_markup=admin_menu())


@router.callback_query(BotUpdateStates.waiting_confirmation, F.data == "bot_update_install")
async def install_update_callback(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    data = await state.get_data()
    archive_path = Path(data.get("archive_path", ""))
    try:
        # Повторная проверка защищает от изменения файла между загрузкой и установкой.
        old_stage = data.get("staging_path")
        if old_stage:
            shutil.rmtree(old_stage, ignore_errors=True)
        inspection = inspect_update_archive(archive_path)
        write_pending_job(inspection, requested_by=call.from_user.id)
    except Exception as exc:
        logger.exception("Повторная проверка архива обновления завершилась ошибкой")
        await state.clear()
        await call.answer("Архив больше не проходит проверку", show_alert=True)
        await call.message.answer(f"❌ Обновление отменено: {html_escape(exc)}")
        return

    await call.answer("Запускаю обновление")
    await call.message.edit_reply_markup(reply_markup=None)

    maintenance_text = (
        "🛠 <b>Производится техническое обслуживание бота</b>\n\n"
        "Дождитесь уведомления перед тем, как продолжить работу. "
        "Действия во время перезапуска могут не сохраниться."
    )
    users = await get_active_users()
    notified: set[int] = set()
    for user in users:
        telegram_id = int(user["telegram_id"])
        try:
            await bot.send_message(telegram_id, maintenance_text)
            notified.add(telegram_id)
        except Exception:
            logger.exception("Не удалось отправить уведомление о техобслуживании пользователю %s", telegram_id)
    if settings.admin_id not in notified:
        try:
            await bot.send_message(settings.admin_id, maintenance_text)
        except Exception:
            logger.exception("Не удалось отправить уведомление о техобслуживании администратору")

    # Последнее Telegram-сообщение отправляем ДО запуска внешнего обновлятора.
    # После systemctl start обновлятор может почти сразу остановить основной процесс,
    # поэтому любые запросы к Telegram после этой точки способны оборваться при закрытии SSL.
    await state.clear()
    await call.message.answer(
        "⏳ Проверки завершены. Запускаю системный обновлятор. Бот сейчас остановится "
        "и перезапустится. Итоговое уведомление придёт после проверки новой версии."
    )

    ok, detail = await start_external_updater()
    if not ok:
        # Если systemd не принял задание, основной бот продолжает работать,
        # поэтому сообщение об ошибке здесь безопасно отправлять.
        await call.message.answer(
            "❌ Не удалось запустить системный обновлятор. Файлы проекта не изменены.\n\n"
            f"<code>{html_escape(detail[:3000])}</code>"
        )
        return
