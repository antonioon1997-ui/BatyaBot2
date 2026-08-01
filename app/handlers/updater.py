from __future__ import annotations

import logging
import shutil
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.config import settings
from app.keyboards.common import bottom_menu_for_role
from app.keyboards.admin import (
    admin_menu,
    admin_ui_activate_confirm_keyboard,
    admin_ui_version_card_keyboard,
    admin_ui_versions_keyboard,
    admin_updates_menu_keyboard,
    update_confirm_keyboard,
)
from app.services.update_manager import (
    INCOMING_DIR,
    ensure_update_directories,
    inspect_update_archive,
    start_external_updater,
    write_pending_job,
)
from app.services.project_export import create_project_export
from app.services.users import get_active_users, is_admin
from app.services.ui_versions import (
    activate_ui_version,
    get_active_ui_id,
    get_ui_version,
    list_ui_versions,
)
from app.states import BotUpdateStates
from app.utils import html_escape

router = Router()
logger = logging.getLogger(__name__)


def _clean_admin_note(value: str) -> str:
    item = str(value).strip()
    for prefix in ("[client]", "[purchasing]", "[all]", "[admin]"):
        if item.lower().startswith(prefix):
            return item[len(prefix):].strip()
    return item


def _inspection_text(inspection) -> str:
    notes = "\n".join(
        f"• {html_escape(_clean_admin_note(item))}"
        for item in inspection.release_notes
        if _clean_admin_note(item)
    )
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


@router.callback_query(F.data == "admin_updates_menu")
async def admin_updates_menu_callback(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    await state.clear()
    await call.message.answer(
        "🔄 <b>Обновления</b>\n\n"
        "Полное обновление меняет код и внутреннюю логику. Версии интерфейса хранятся отдельно, "
        "поэтому их можно переключать без отката исправлений, базы данных и безопасности.\n\n"
        "Система хранит до пяти версий интерфейса. Аварийные технические копии обновлятор продолжает "
        "создавать отдельно перед установкой каждого патча.",
        reply_markup=admin_updates_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin_export_project")
async def admin_export_project_callback(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    await call.answer("Готовлю архив")
    status_message = await call.message.answer(
        "⏳ Собираю безопасный архив текущей версии без базы, токенов, логов и резервных копий..."
    )
    export_path = None
    try:
        export_path, file_count, digest = create_project_export()
        await call.message.answer_document(
            FSInputFile(export_path, filename=export_path.name),
            caption=(
                "📤 <b>Текущая версия BatyaBot2</b>\n\n"
                f"Файлов: <b>{file_count}</b>\n"
                f"SHA-256: <code>{digest}</code>\n\n"
                "В архив не включены .env, токены, рабочая база, резервные копии, логи, venv и Git-метаданные."
            ),
        )
        await status_message.delete()
    except Exception as exc:
        logger.exception("Не удалось выгрузить текущую версию проекта")
        await status_message.edit_text(
            f"❌ Не удалось собрать архив: <code>{html_escape(str(exc)[:1000])}</code>"
        )
    finally:
        if export_path is not None:
            Path(export_path).unlink(missing_ok=True)


@router.callback_query(F.data == "admin_update_history")
async def admin_update_history_callback(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    history_file = Path(__file__).resolve().parents[2] / "runtime" / "update_history.json"
    entries = []
    try:
        import json
        loaded = json.loads(history_file.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            entries = [item for item in loaded if isinstance(item, dict)][-5:]
    except (OSError, ValueError):
        entries = []

    if not entries:
        history_text = "📋 <b>История обновлений</b>\n\nИстория пока пуста."
    else:
        blocks = []
        for item in reversed(entries):
            changes = []
            for raw in item.get("changes", []):
                value = str(raw).strip()
                for prefix in ("[client]", "[purchasing]", "[all]", "[admin]"):
                    if value.lower().startswith(prefix):
                        value = value[len(prefix):].strip()
                        break
                if value:
                    changes.append(f"• {html_escape(value)}")
            blocks.append(
                f"<b>Версия {html_escape(item.get('version'))}</b>\n"
                f"{html_escape(item.get('date'))}\n"
                + ("\n".join(changes) or "• Техническое обновление")
            )
        history_text = "📋 <b>Последние обновления</b>\n\n" + "\n\n".join(blocks)

    await call.message.answer(history_text, reply_markup=admin_updates_menu_keyboard())
    await call.answer()


@router.callback_query(F.data == "admin_ui_versions")
async def admin_ui_versions_callback(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    versions = list_ui_versions()
    active_id = get_active_ui_id()
    await call.message.answer(
        "🎨 <b>Версии интерфейса</b>\n\n"
        "Переключение меняет только доступные пользовательские меню и стиль системных фраз. "
        "Тикеты, база данных, исправления, архитектура и производительность не откатываются.\n\n"
        f"Хранится версий: <b>{len(versions)}</b> из 5.",
        reply_markup=admin_ui_versions_keyboard(versions, active_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_version:"))
async def admin_ui_version_callback(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    version_id = call.data.split(":", 1)[1]
    profile = get_ui_version(version_id)
    if not profile:
        await call.answer("Версия интерфейса не найдена.", show_alert=True)
        return
    is_active = version_id == get_active_ui_id()
    config = profile.get("config", {})
    await call.message.answer(
        f"🎨 <b>{html_escape(profile.get('title'))}</b>\n\n"
        f"Версия приложения: {html_escape(profile.get('app_version'))}\n"
        f"Статус: {'✅ используется сейчас' if is_active else 'доступна для применения'}\n\n"
        f"{html_escape(profile.get('description'))}\n\n"
        f"Центр помощи: {'да' if config.get('show_help_button') else 'нет'}\n"
        f"Настройки стиля: {'да' if config.get('show_help_settings') else 'нет'}\n"
        f"Дружелюбные фразы: {'да' if config.get('allow_friendly_style') else 'нет'}",
        reply_markup=admin_ui_version_card_keyboard(version_id, is_active),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_activate:"))
async def admin_ui_activate_callback(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    version_id = call.data.split(":", 1)[1]
    profile = get_ui_version(version_id)
    if not profile:
        await call.answer("Версия интерфейса не найдена.", show_alert=True)
        return
    await call.message.answer(
        f"↩️ <b>Применить «{html_escape(profile.get('title'))}»?</b>\n\n"
        "Будут изменены только пользовательские меню и варианты коротких системных сообщений.\n\n"
        "Не будут затронуты:\n"
        "• база данных и существующие тикеты;\n"
        "• исправления ошибок и безопасность;\n"
        "• архитектура и производительность;\n"
        "• установленная версия кода.",
        reply_markup=admin_ui_activate_confirm_keyboard(version_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_ui_confirm:"))
async def admin_ui_confirm_callback(call: CallbackQuery, bot: Bot):
    if not await is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    version_id = call.data.split(":", 1)[1]
    try:
        profile = activate_ui_version(version_id)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return

    users = await get_active_users()
    refreshed = 0
    for user in users:
        telegram_id = int(user["telegram_id"])
        try:
            await bot.send_message(
                telegram_id,
                "🎨 Интерфейс бота обновлён. Нижнее меню синхронизировано.",
                reply_markup=bottom_menu_for_role(
                    user["role"],
                    is_admin=telegram_id == int(settings.admin_id),
                ),
            )
            refreshed += 1
        except Exception:
            logger.exception("Не удалось обновить меню пользователя %s", telegram_id)

    await call.message.answer(
        f"✅ Применена версия интерфейса: <b>{html_escape(profile.get('title'))}</b>.\n\n"
        f"Нижнее меню отправлено пользователям: {refreshed}.",
        reply_markup=admin_updates_menu_keyboard(),
    )
    await call.answer("Интерфейс переключён")


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
