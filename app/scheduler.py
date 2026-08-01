from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import logging

from app.config import settings
from app.services.tickets import (
    get_overdue_client_tickets,
    get_active_users_by_department,
    get_setting,
    get_due_auto_close_tickets,
    close_due_auto_close_ticket,
    get_ticket_by_id,
)
from app.keyboards.productivity import daily_summary_confirm_keyboard, restore_day_off_keyboard
from app.keyboards.tickets import open_ticket_keyboard, overdue_tickets_keyboard
from app.services.analytics import build_daily_summary_text, collect_daily_stats, mark_daily_summary_admin_sent, save_daily_summary
from app.services.backups import create_database_backup
from app.services.ticket_messages import send_live_ticket_text
from app.services.work_management import (
    activate_scheduled_day_offs,
    expire_finished_day_offs,
    get_due_snoozed_tickets,
    wake_snoozed_ticket,
)
from app.utils import html_escape


logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.timezone)


def row_get(row, key, default=None):
    if row is None:
        return default

    try:
        if key in row.keys():
            return row[key]
    except Exception:
        return default

    return default


def has_text_value(value) -> bool:
    if value is None:
        return False

    return bool(str(value).strip())


def short_text(value, limit: int = 80) -> str:
    if value is None:
        return "—"

    value = str(value).strip()

    if not value:
        return "—"

    if len(value) <= limit:
        return value

    return value[:limit].rstrip() + "..."


def format_overdue_ticket_line(ticket) -> str:
    ticket_id = row_get(ticket, "id")
    title = html_escape(short_text(row_get(ticket, "title"), 90))
    order_number = row_get(ticket, "order_number")
    overdue_days = int(row_get(ticket, "overdue_days", 0) or 0)

    order_part = ""

    if has_text_value(order_number):
        order_part = f" Заказ: {html_escape(str(order_number).strip())} —"

    return f"#{ticket_id}{order_part} {title} ({overdue_days} дн.)"


def build_overdue_text(tickets, page: int = 0, page_size: int = 10) -> str:
    total = len(tickets)
    total_pages = (total + page_size - 1) // page_size if total else 1

    if page < 0:
        page = 0

    if page >= total_pages:
        page = max(total_pages - 1, 0)

    start = page * page_size
    end = start + page_size
    page_tickets = tickets[start:end]

    urgent_tickets = []
    warning_tickets = []

    for ticket in page_tickets:
        overdue_days = int(row_get(ticket, "overdue_days", 0) or 0)

        if overdue_days >= 4:
            urgent_tickets.append(ticket)
        else:
            warning_tickets.append(ticket)

    text = (
        "⏰ <b>Напоминание по просроченным тикетам</b>\n\n"
        "Это тикеты от отдела закупки, которые ждут обработки клиентским отделом.\n\n"
    )

    if total_pages > 1:
        text += f"Страница {page + 1} из {total_pages}. Всего тикетов: {total}\n\n"

    if warning_tickets:
        text += "⚠️ <b>Открыт более 2 дней:</b>\n"
        for ticket in warning_tickets:
            text += format_overdue_ticket_line(ticket) + "\n"
        text += "\n"

    if urgent_tickets:
        text += "🚨 <b>Открыт более 4 дней, срочно обработать:</b>\n"
        for ticket in urgent_tickets:
            text += format_overdue_ticket_line(ticket) + "\n"
        text += "\n"

    text += "Открой нужный тикет кнопкой ниже."

    return text


async def send_overdue_client_reminders(bot):
    tickets = await get_overdue_client_tickets()

    if not tickets:
        return

    users = await get_active_users_by_department("client")

    if not users:
        return

    text = build_overdue_text(tickets, page=0, page_size=10)
    keyboard = overdue_tickets_keyboard(tickets, page=0, page_size=10)

    for user in users:
        try:
            await bot.send_message(
                chat_id=user["telegram_id"],
                text=text,
                reply_markup=keyboard
            )
        except Exception:
            logger.exception("Не удалось отправить напоминание пользователю %s", user["telegram_id"])


async def process_due_ticket_auto_closures(bot):
    tickets = await get_due_auto_close_tickets()

    for ticket in tickets:
        ticket_id = int(row_get(ticket, "id", 0) or 0)
        if not ticket_id:
            continue

        try:
            closed = await close_due_auto_close_ticket(ticket_id)
            if not closed:
                continue

            updated_ticket = await get_ticket_by_id(ticket_id)
            creator_id = int(row_get(updated_ticket, "created_by", 0) or 0)

            if creator_id:
                try:
                    await send_live_ticket_text(
                        bot,
                        chat_id=creator_id,
                        ticket_id=ticket_id,
                        text=f"✅ Тикет #{ticket_id} автоматически закрыт как выполненный.",
                        reply_markup=open_ticket_keyboard(ticket_id),
                    )
                except Exception:
                    logger.exception(
                        "Не удалось уведомить автора об автозакрытии тикета %s",
                        ticket_id,
                    )

            users = await get_active_users_by_department(
                row_get(updated_ticket, "executor_department")
            )
            for user in users:
                telegram_id = int(user["telegram_id"])
                try:
                    await send_live_ticket_text(
                        bot,
                        chat_id=telegram_id,
                        ticket_id=ticket_id,
                        text=f"✅ Тикет #{ticket_id} автоматически закрыт как выполненный.",
                        reply_markup=open_ticket_keyboard(ticket_id),
                    )
                except Exception:
                    logger.exception(
                        "Не удалось уведомить пользователя %s об автозакрытии тикета %s",
                        telegram_id,
                        ticket_id,
                    )
        except Exception:
            logger.exception("Ошибка автоматического закрытия тикета %s", ticket_id)


async def process_snoozed_tickets(bot):
    tickets = await get_due_snoozed_tickets()
    for ticket in tickets:
        ticket_id = int(row_get(ticket, "id", 0) or 0)
        if not ticket_id:
            continue
        try:
            if not await wake_snoozed_ticket(ticket_id):
                continue
            assignee = int(row_get(ticket, "taken_by", 0) or 0)
            if assignee:
                recipients = [{"telegram_id": assignee}]
            else:
                recipients = await get_active_users_by_department(row_get(ticket, "executor_department"))
            for user in recipients:
                try:
                    await send_live_ticket_text(
                        bot,
                        chat_id=int(user["telegram_id"]),
                        ticket_id=ticket_id,
                        text=f"⏰ Срок отложения тикета #{ticket_id} завершён. Он снова доступен в рабочих списках.",
                        reply_markup=open_ticket_keyboard(ticket_id),
                    )
                except Exception:
                    logger.exception("Не удалось уведомить о возврате отложенного тикета %s", ticket_id)
        except Exception:
            logger.exception("Ошибка возврата отложенного тикета %s", ticket_id)


async def process_day_off_starts(bot):
    try:
        releases = await activate_scheduled_day_offs()
        for user_id, ticket_ids in releases:
            try:
                await bot.send_message(
                    user_id,
                    f"🏖 Начался отмеченный выходной. В общий список возвращено тикетов: {len(ticket_ids)}.",
                )
            except Exception:
                logger.exception("Не удалось уведомить пользователя %s о начале выходного", user_id)
    except Exception:
        logger.exception("Ошибка обработки начала выходных")


async def process_day_off_ends(bot):
    try:
        finished = await expire_finished_day_offs()
        for user_id, ticket_ids in finished:
            try:
                text = "🟢 Отмеченный период выходных завершён."
                markup = None
                if ticket_ids:
                    text += (
                        f" Свободных тикетов, которые были сняты с тебя на выходные: {len(ticket_ids)}.\n\n"
                        "Их можно вернуть себе, если они всё ещё никем не заняты."
                    )
                    markup = restore_day_off_keyboard(user_id)
                await bot.send_message(user_id, text, reply_markup=markup)
            except Exception:
                logger.exception("Не удалось уведомить пользователя %s об окончании выходного", user_id)
    except Exception:
        logger.exception("Ошибка обработки окончания выходных")


async def send_daily_admin_summary(bot):
    try:
        row = await collect_daily_stats()
        text = build_daily_summary_text(row)
        if not await save_daily_summary(row["stat_date"], text):
            return
        await bot.send_message(
            settings.admin_id,
            text,
            reply_markup=daily_summary_confirm_keyboard(row["stat_date"]),
        )
        await mark_daily_summary_admin_sent(row["stat_date"])
    except Exception:
        logger.exception("Не удалось сформировать вечернюю сводку")


async def setup_daily_reminder_job(bot):
    reminder_time = await get_setting("reminder_time", settings.reminder_time)

    if not reminder_time:
        reminder_time = "08:50"

    try:
        hour_raw, minute_raw = reminder_time.split(":")
        hour = int(hour_raw)
        minute = int(minute_raw)
    except Exception:
        logger.warning("Некорректное время напоминания %r; используется 08:50", reminder_time)
        hour = 8
        minute = 50

    scheduler.add_job(
        send_overdue_client_reminders,
        CronTrigger(hour=hour, minute=minute),
        args=[bot],
        id="daily_overdue_client_reminders",
        replace_existing=True
    )


async def run_monthly_backup():
    try:
        await create_database_backup(keep_last=10)
    except Exception:
        logger.exception("Ежемесячное резервное копирование завершилось ошибкой")


def start_scheduler(bot):
    scheduler.add_job(
        process_due_ticket_auto_closures,
        IntervalTrigger(minutes=1),
        args=[bot],
        id="ticket_auto_close_sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        process_snoozed_tickets,
        IntervalTrigger(minutes=1),
        args=[bot],
        id="ticket_snooze_sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        process_day_off_starts,
        IntervalTrigger(minutes=10),
        args=[bot],
        id="day_off_start_sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        process_day_off_ends,
        IntervalTrigger(minutes=10),
        args=[bot],
        id="day_off_end_sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        send_daily_admin_summary,
        CronTrigger(hour=21, minute=10, timezone="Europe/Moscow"),
        args=[bot],
        id="daily_admin_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Страховочный повтор: если Telegram был недоступен в 21:10, функция
    # повторит отправку в 21:20; защита в БД не допустит дубль после успеха.
    scheduler.add_job(
        send_daily_admin_summary,
        CronTrigger(hour=21, minute=20, timezone="Europe/Moscow"),
        args=[bot],
        id="daily_admin_summary_retry",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        run_monthly_backup,
        CronTrigger(day=1, hour=3, minute=0),
        id="monthly_database_backup",
        replace_existing=True,
    )

    scheduler.add_job(
        setup_daily_reminder_job,
        args=[bot],
        id="setup_daily_overdue_reminder_job",
        replace_existing=True
    )

    scheduler.start()