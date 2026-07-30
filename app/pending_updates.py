import logging
from collections import defaultdict

from aiogram import Bot

from app.config import settings

logger = logging.getLogger(__name__)


def describe_update(update) -> tuple[int | None, str]:
    if update.message:
        user = update.message.from_user
        text = update.message.text or update.message.caption or "[вложение/служебное сообщение]"
        return (user.id if user else None, f"Сообщение: {text[:300]}")
    if update.callback_query:
        user = update.callback_query.from_user
        return (user.id if user else None, f"Нажатие кнопки: {update.callback_query.data or '[без данных]'}")
    return None, f"Обновление типа {update.event_type}"


async def collect_and_discard_pending_updates(bot: Bot) -> int:
    """Собирает накопленные обновления, сообщает админу и удаляет их из очереди."""
    updates = await bot.get_updates(timeout=0, limit=100)
    if not updates:
        return 0

    grouped: dict[int | None, list[str]] = defaultdict(list)
    max_update_id = 0
    for update in updates:
        max_update_id = max(max_update_id, update.update_id)
        user_id, description = describe_update(update)
        grouped[user_id].append(description)

    lines = [
        "⚠️ <b>Во время остановки бота накопились действия</b>",
        "",
        "Эти действия не выполнены и удалены из очереди. Попросите пользователей повторить их.",
        "",
    ]
    for user_id, actions in grouped.items():
        lines.append(f"👤 Пользователь <code>{user_id or 'не определён'}</code>:")
        lines.extend(f"• {action}" for action in actions[:20])
        if len(actions) > 20:
            lines.append(f"• …ещё {len(actions)-20}")
        lines.append("")

    text = "\n".join(lines)
    try:
        await bot.send_message(settings.admin_id, text[:4000])
    except Exception:
        logger.exception("Не удалось отправить отчёт о накопленных обновлениях")

    await bot.get_updates(offset=max_update_id + 1, timeout=0, limit=1)
    logger.warning("Удалено накопленных обновлений: %s", len(updates))
    return len(updates)
