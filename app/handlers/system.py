import logging
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import ErrorEvent, Message
from aiogram.exceptions import TelegramNetworkError

from app.config import settings
from app.database import get_db
from app.scheduler import scheduler
from app.services.backups import create_database_backup
from app.services.update_manager import JOB_FILE
from app.services.tickets import get_admin_ticket_stats
from app.services.users import is_admin
from app.version import get_version
from app.utils import format_moscow_datetime, html_escape, moscow_now

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("health"))
async def health_command(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    db_status = "✅ доступна"
    try:
        db = await get_db()
        await db.execute("SELECT 1")
        await db.close()
    except Exception as exc:
        logger.exception("Ошибка проверки базы")
        db_status = f"❌ ошибка: {type(exc).__name__}"

    scheduler_status = "✅ запущен" if scheduler.running else "❌ не запущен"
    jobs = scheduler.get_jobs() if scheduler.running else []
    job_lines = "\n".join(f"• {html_escape(job.id)}: {format_moscow_datetime(job.next_run_time) if job.next_run_time else 'нет следующего запуска'}" for job in jobs) or "• заданий нет"

    try:
        stats = await get_admin_ticket_stats()
        stats_text = (
            f"Всего тикетов: {stats['total'] or 0}\n"
            f"Открытых: {stats['open_total'] or 0}\n"
            f"Закрытых: {stats['closed_total'] or 0}"
        )
    except Exception:
        logger.exception("Ошибка получения статистики для /health")
        stats_text = "Статистика недоступна"

    await message.answer(
        "🩺 <b>Состояние бота</b>\n\n"
        f"Версия: <b>{get_version()}</b>\n"
        f"Время проверки: {moscow_now().strftime('%d.%m.%Y %H:%M МСК')}\n"
        f"База данных: {db_status}\n"
        f"Планировщик: {scheduler_status}\n\n"
        f"<b>Задания планировщика:</b>\n{job_lines}\n\n"
        f"<b>Тикеты:</b>\n{stats_text}"
    )


@router.message(Command("backup"))
async def backup_command(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    try:
        path = await create_database_backup(keep_last=10)
        await message.answer(f"✅ Резервная копия создана и проверена:\n<code>{html_escape(path.name)}</code>")
    except Exception as exc:
        logger.exception("Ручное резервное копирование завершилось ошибкой")
        await message.answer(f"❌ Не удалось создать копию: {html_escape(type(exc).__name__)}: {html_escape(exc)}")


@router.errors()
async def global_error_handler(event: ErrorEvent, bot: Bot):
    # При обновлении внешний systemd-процесс останавливает polling. Иногда aiohttp/OpenSSL
    # в этот момент сообщает APPLICATION_DATA_AFTER_CLOSE_NOTIFY: соединение уже закрывается,
    # но в сокет пришёл последний пакет. Это ожидаемое завершение, а не поломка бота.
    if (
        isinstance(event.exception, TelegramNetworkError)
        and "APPLICATION_DATA_AFTER_CLOSE_NOTIFY" in str(event.exception)
        and JOB_FILE.exists()
    ):
        logger.info("Сетевая сессия Telegram закрыта во время штатного обновления")
        return True

    logger.exception("Необработанная ошибка при обработке обновления", exc_info=event.exception)

    update = event.update
    user_id = None
    try:
        if update.message and update.message.from_user:
            user_id = update.message.from_user.id
            await update.message.answer("⚠️ Произошла внутренняя ошибка. Администратор уже получит сведения о ней.")
        elif update.callback_query and update.callback_query.from_user:
            user_id = update.callback_query.from_user.id
            await update.callback_query.answer("Произошла внутренняя ошибка.", show_alert=True)
    except Exception:
        logger.exception("Не удалось уведомить пользователя об ошибке")

    try:
        await bot.send_message(
            settings.admin_id,
            "🚨 <b>Ошибка в боте</b>\n\n"
            f"Пользователь: <code>{user_id or 'не определён'}</code>\n"
            f"Тип: <code>{html_escape(type(event.exception).__name__)}</code>\n"
            f"Текст: <code>{html_escape(str(event.exception)[:2500])}</code>"
        )
    except Exception:
        logger.exception("Не удалось отправить администратору сообщение об ошибке")

    return True
