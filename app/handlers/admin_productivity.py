from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.config import settings
from app.keyboards.productivity import (
    admin_analytics_keyboard,
    restore_day_off_keyboard,
)
from app.services.analytics import (
    collect_daily_stats,
    export_statistics_csv,
    get_daily_summary,
    mark_daily_summary_observers_sent,
)
from app.services.users import get_user_by_telegram_id, get_users_by_role
from app.services.work_management import clear_day_off, restore_day_off_tickets, set_day_off

router = Router()
logger = logging.getLogger(__name__)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _is_admin(telegram_id: int) -> bool:
    return int(telegram_id) == int(settings.admin_id)


async def _deny(call_or_message) -> bool:
    if _is_admin(call_or_message.from_user.id):
        return False
    if isinstance(call_or_message, CallbackQuery):
        await call_or_message.answer("Нет доступа.", show_alert=True)
    else:
        await call_or_message.answer("Нет доступа.")
    return True


@router.callback_query(F.data == "admin_templates")
@router.callback_query(F.data.startswith("admin_tpl"))
async def callback_admin_templates_disabled(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    await call.answer("Шаблоны ответов отключены.", show_alert=True)


@router.callback_query(F.data == "admin_analytics")
async def callback_admin_analytics(call: CallbackQuery):
    if await _deny(call):
        return
    row = await collect_daily_stats()
    await call.message.answer(
        "📈 <b>Статистика и накопление метрик</b>\n\n"
        f"Открытых тикетов: <b>{row['total_open']}</b>\n"
        f"Новых: {row['total_new']}\n"
        f"В работе: {row['total_in_work']}\n"
        f"Ожидают: {row['total_waiting']}\n"
        f"Отложено: {row['total_snoozed']}\n"
        f"Без исполнителя: {row['total_unassigned']}\n\n"
        f"Создано сегодня: {row['created_today']}\n"
        f"Закрыто сегодня: {row['closed_today']}\n"
        f"Открыты более двух дней: {row['overdue_total']}\n\n"
        "CSV содержит тикеты, время первого взятия, первого ответа, первого завершения, возвраты и назначения, а также ежедневные снимки.",
        reply_markup=admin_analytics_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin_stats_export")
async def callback_admin_stats_export(call: CallbackQuery):
    if await _deny(call):
        return
    payload = await export_statistics_csv()
    filename = f"batyabot_stats_{datetime.now(MOSCOW_TZ).strftime('%Y%m%d_%H%M')}.csv"
    await call.message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption="📄 Выгрузка накопленной статистики. Время в полях с суффиксом UTC хранится в UTC.",
    )
    await call.answer()


@router.callback_query(F.data.startswith("daily_summary_confirm:"))
async def callback_daily_summary_confirm(call: CallbackQuery):
    if await _deny(call):
        return
    stat_date = call.data.split(":", 1)[1]
    summary = await get_daily_summary(stat_date)
    if not summary:
        await call.answer("Сводка не найдена.", show_alert=True)
        return
    if summary["sent_to_observers_at"]:
        await call.answer("Эта сводка уже отправлена наблюдателям.", show_alert=True)
        return
    observers = await get_users_by_role("observer")
    delivered = 0
    failed = 0
    observer_text = str(summary["summary_text"]).replace(
        "\n\nПосле проверки нажми кнопку, чтобы отправить эту же сводку наблюдателям.",
        "",
    )
    for observer in observers:
        try:
            await call.bot.send_message(int(observer["telegram_id"]), observer_text)
            delivered += 1
        except Exception:
            failed += 1
            logger.exception("Не удалось отправить сводку наблюдателю %s", observer["telegram_id"])
    await mark_daily_summary_observers_sent(stat_date, call.from_user.id)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"✅ Сводка отправлена наблюдателям.\nПолучателей: {len(observers)}\nДоставлено: {delivered}\nОшибок: {failed}"
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_dayoff_today:"))
async def callback_admin_dayoff_today(call: CallbackQuery):
    if await _deny(call):
        return
    user_id = int(call.data.split(":")[1])
    user = await get_user_by_telegram_id(user_id)
    if not user:
        await call.answer("Пользователь не найден.", show_alert=True)
        return
    start, end, released = await set_day_off(user_id, 0, 1, call.from_user.id)
    try:
        await call.bot.send_message(
            user_id,
            f"🏖 Администратор отметил тебе выходной на {start}. Назначенные тикеты возвращены в общий список: {len(released)}.",
        )
    except Exception:
        logger.exception("Не удалось уведомить пользователя %s о выходном", user_id)
    await call.message.answer(f"🏖 Выходной установлен: {start} — {end}. Освобождено тикетов: {len(released)}.")
    await call.answer()


@router.callback_query(F.data.startswith("admin_dayoff_clear:"))
async def callback_admin_dayoff_clear(call: CallbackQuery):
    if await _deny(call):
        return
    user_id = int(call.data.split(":")[1])
    candidates = await clear_day_off(user_id, call.from_user.id)
    if candidates:
        try:
            await call.bot.send_message(
                user_id,
                f"🟢 Администратор убрал статус выходного. Свободных ранее назначенных тикетов: {len(candidates)}. Вернуть их себе?",
                reply_markup=restore_day_off_keyboard(user_id),
            )
        except Exception:
            logger.exception("Не удалось отправить предложение возврата тикетов %s", user_id)
    await call.message.answer(f"🟢 Выходной убран. Доступно для возврата: {len(candidates)}.")
    await call.answer()
