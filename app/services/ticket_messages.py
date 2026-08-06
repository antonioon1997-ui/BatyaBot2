from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable

from app.database import get_db

logger = logging.getLogger(__name__)

SAFE_TEXT_LIMIT = 3800

_MESSAGE_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


def _message_lock(ticket_id: int, chat_id: int) -> asyncio.Lock:
    key = (int(ticket_id), int(chat_id))
    lock = _MESSAGE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _MESSAGE_LOCKS[key] = lock
    return lock


def _split_text(text: str, limit: int = SAFE_TEXT_LIMIT) -> list[str]:
    value = str(text or "")
    if len(value) <= limit:
        return [value]

    chunks: list[str] = []
    current = ""
    for paragraph in value.split("\n"):
        addition = paragraph if not current else "\n" + paragraph
        if len(current) + len(addition) <= limit:
            current += addition
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks or [""]


def _normalize_message_ids(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id <= 0 or message_id in seen:
            continue
        seen.add(message_id)
        result.append(message_id)
    return result


async def get_ticket_message_ids(ticket_id: int, user_id: int) -> list[int]:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT message_ids_json
            FROM ticket_message_registry
            WHERE ticket_id = ? AND user_id = ?
            """,
            (int(ticket_id), int(user_id)),
        )
        row = await cursor.fetchone()
        if not row:
            return []
        try:
            payload = json.loads(row["message_ids_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return _normalize_message_ids(payload if isinstance(payload, list) else [])
    finally:
        await db.close()


async def set_ticket_message_ids(ticket_id: int, user_id: int, message_ids: Iterable[int]) -> list[int]:
    normalized = _normalize_message_ids(message_ids)
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO ticket_message_registry (
                ticket_id, user_id, message_ids_json, updated_at
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id, user_id) DO UPDATE SET
                message_ids_json = excluded.message_ids_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(ticket_id), int(user_id), json.dumps(normalized)),
        )
        await db.commit()
        return normalized
    finally:
        await db.close()


async def delete_message_ids(bot, chat_id: int, message_ids: Iterable[int]) -> None:
    for message_id in _normalize_message_ids(message_ids):
        try:
            await bot.delete_message(chat_id=int(chat_id), message_id=message_id)
        except Exception:
            # Старое сообщение могло быть удалено вручную или уже стать недоступным.
            logger.debug(
                "Не удалось удалить старое сообщение %s в чате %s",
                message_id,
                chat_id,
                exc_info=True,
            )


async def replace_ticket_message_bundle(
    bot,
    *,
    chat_id: int,
    ticket_id: int,
    new_message_ids: Iterable[int],
) -> list[int]:
    """Запоминает новый комплект сообщений и затем удаляет предыдущий.

    Новый комплект сохраняется первым: если Telegram временно не даст удалить старый,
    следующее обновление карточки всё равно будет работать с актуальными ID.
    """
    normalized = _normalize_message_ids(new_message_ids)
    async with _message_lock(ticket_id, chat_id):
        old_ids = await get_ticket_message_ids(ticket_id, chat_id)
        await set_ticket_message_ids(ticket_id, chat_id, normalized)
        new_set = set(normalized)
        await delete_message_ids(bot, chat_id, (item for item in old_ids if item not in new_set))
    return normalized


async def clear_ticket_message_bundle(bot, *, chat_id: int, ticket_id: int) -> None:
    async with _message_lock(ticket_id, chat_id):
        old_ids = await get_ticket_message_ids(ticket_id, chat_id)
        await set_ticket_message_ids(ticket_id, chat_id, [])
        await delete_message_ids(bot, chat_id, old_ids)


async def send_live_ticket_text(
    bot,
    *,
    chat_id: int,
    ticket_id: int,
    text: str,
    reply_markup=None,
) -> list[int]:
    """Отправляет уведомление по тикету как единственный актуальный комплект."""
    sent_ids: list[int] = []
    chunks = _split_text(text)
    try:
        for index, chunk in enumerate(chunks):
            message = await bot.send_message(
                chat_id=int(chat_id),
                text=chunk,
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
            )
            sent_ids.append(int(message.message_id))
    except Exception:
        await delete_message_ids(bot, chat_id, sent_ids)
        raise

    try:
        await replace_ticket_message_bundle(
            bot,
            chat_id=int(chat_id),
            ticket_id=int(ticket_id),
            new_message_ids=sent_ids,
        )
    except Exception:
        # Если база временно недоступна, не оставляем новый незарегистрированный дубль.
        # Предыдущая карточка при этом остаётся рабочей.
        await delete_message_ids(bot, chat_id, sent_ids)
        raise
    return sent_ids
