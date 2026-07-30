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
    admin_template_card_keyboard,
    admin_templates_keyboard,
    restore_day_off_keyboard,
)
from app.services.analytics import (
    collect_daily_stats,
    export_statistics_csv,
    get_daily_summary,
    mark_daily_summary_observers_sent,
)
from app.services.templates import (
    create_response_template,
    get_response_template,
    get_response_templates,
    update_response_template,
)
from app.services.users import get_user_by_telegram_id, get_users_by_role
from app.services.work_management import clear_day_off, restore_day_off_tickets, set_day_off
from app.states import AdminProductivityStates
from app.utils import html_escape

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


def _template_card_text(template) -> str:
    return (
        f"💬 <b>Шаблон #{template['id']}</b>\n\n"
        f"Название: <b>{html_escape(template['title'])}</b>\n"
        f"Статус: {'активен' if template['is_active'] else 'отключён'}\n\n"
        f"Текст:\n{html_escape(template['body'])}"
    )


@router.callback_query(F.data == "admin_templates")
async def callback_admin_templates(call: CallbackQuery):
    if await _deny(call):
        return
    templates = await get_response_templates("purchasing", include_inactive=True)
    await call.message.answer(
        "💬 <b>Шаблоны ответов отдела закупки</b>\n\n"
        "Отключённые шаблоны сохраняются, но не показываются сотрудникам при ответе.",
        reply_markup=admin_templates_keyboard(templates),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admin_tpl:") & ~F.data.startswith("admin_tpl_add"))
async def callback_admin_template_card(call: CallbackQuery):
    if await _deny(call):
        return
    template_id = int(call.data.split(":")[1])
    template = await get_response_template(template_id)
    if not template:
        await call.answer("Шаблон не найден.", show_alert=True)
        return
    await call.message.answer(
        _template_card_text(template),
        reply_markup=admin_template_card_keyboard(template_id, bool(template["is_active"])),
    )
    await call.answer()


@router.callback_query(F.data == "admin_tpl_add")
async def callback_admin_template_add(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    await state.set_state(AdminProductivityStates.waiting_template_title)
    await call.message.answer("Напиши короткое название нового шаблона.")
    await call.answer()


@router.message(AdminProductivityStates.waiting_template_title)
async def process_admin_template_title(message: Message, state: FSMContext):
    if await _deny(message):
        await state.clear()
        return
    if not message.text or not message.text.strip():
        await message.answer("Название должно быть текстом.")
        return
    await state.update_data(template_title=message.text.strip()[:80])
    await state.set_state(AdminProductivityStates.waiting_template_body)
    await message.answer("Теперь отправь полный текст шаблона.")


@router.message(AdminProductivityStates.waiting_template_body)
async def process_admin_template_body(message: Message, state: FSMContext):
    if await _deny(message):
        await state.clear()
        return
    if not message.text or not message.text.strip():
        await message.answer("Текст шаблона не может быть пустым.")
        return
    data = await state.get_data()
    template_id = await create_response_template(
        data["template_title"],
        message.text.strip()[:3000],
        message.from_user.id,
        "purchasing",
    )
    await state.clear()
    template = await get_response_template(template_id)
    await message.answer(
        "✅ Шаблон создан.\n\n" + _template_card_text(template),
        reply_markup=admin_template_card_keyboard(template_id, True),
    )


@router.callback_query(F.data.startswith("admin_tpl_edit_title:"))
async def callback_admin_template_edit_title(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    template_id = int(call.data.split(":")[1])
    if not await get_response_template(template_id):
        await call.answer("Шаблон не найден.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminProductivityStates.waiting_template_edit_title)
    await state.update_data(template_id=template_id)
    await call.message.answer("Отправь новое название шаблона.")
    await call.answer()


@router.message(AdminProductivityStates.waiting_template_edit_title)
async def process_admin_template_edit_title(message: Message, state: FSMContext):
    if await _deny(message):
        await state.clear()
        return
    if not message.text or not message.text.strip():
        await message.answer("Название должно быть текстом.")
        return
    data = await state.get_data()
    await update_response_template(int(data["template_id"]), title=message.text.strip()[:80])
    template = await get_response_template(int(data["template_id"]))
    await state.clear()
    await message.answer(
        "✅ Название обновлено.\n\n" + _template_card_text(template),
        reply_markup=admin_template_card_keyboard(template["id"], bool(template["is_active"])),
    )


@router.callback_query(F.data.startswith("admin_tpl_edit_body:"))
async def callback_admin_template_edit_body(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    template_id = int(call.data.split(":")[1])
    if not await get_response_template(template_id):
        await call.answer("Шаблон не найден.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminProductivityStates.waiting_template_edit_body)
    await state.update_data(template_id=template_id)
    await call.message.answer("Отправь новый полный текст шаблона.")
    await call.answer()


@router.message(AdminProductivityStates.waiting_template_edit_body)
async def process_admin_template_edit_body(message: Message, state: FSMContext):
    if await _deny(message):
        await state.clear()
        return
    if not message.text or not message.text.strip():
        await message.answer("Текст шаблона не может быть пустым.")
        return
    data = await state.get_data()
    await update_response_template(int(data["template_id"]), body=message.text.strip()[:3000])
    template = await get_response_template(int(data["template_id"]))
    await state.clear()
    await message.answer(
        "✅ Текст обновлён.\n\n" + _template_card_text(template),
        reply_markup=admin_template_card_keyboard(template["id"], bool(template["is_active"])),
    )


@router.callback_query(F.data.startswith("admin_tpl_toggle:"))
async def callback_admin_template_toggle(call: CallbackQuery):
    if await _deny(call):
        return
    template_id = int(call.data.split(":")[1])
    template = await get_response_template(template_id)
    if not template:
        await call.answer("Шаблон не найден.", show_alert=True)
        return
    await update_response_template(template_id, is_active=not bool(template["is_active"]))
    updated = await get_response_template(template_id)
    await call.message.answer(
        _template_card_text(updated),
        reply_markup=admin_template_card_keyboard(template_id, bool(updated["is_active"])),
    )
    await call.answer("Статус изменён.")


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
