from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.database import get_db
from app.services.ticket_messages import delete_message_ids

logger = logging.getLogger(__name__)

PRIMARY_UI_SLOT = "primary"
SAFE_TEXT_LIMIT = 3800

_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


@dataclass(frozen=True)
class UiMessagePart:
    text: str
    reply_markup: object | None = None


def _slot_lock(user_id: int, slot: str) -> asyncio.Lock:
    key = (int(user_id), str(slot))
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


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


def split_ui_text(text: str, limit: int = SAFE_TEXT_LIMIT) -> list[str]:
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


async def get_ui_message_ids(user_id: int, slot: str = PRIMARY_UI_SLOT) -> list[int]:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT message_ids_json
            FROM ui_message_registry
            WHERE user_id = ? AND slot = ?
            """,
            (int(user_id), str(slot)),
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


async def set_ui_message_ids(
    user_id: int,
    slot: str,
    message_ids: Iterable[int],
) -> list[int]:
    normalized = _normalize_message_ids(message_ids)
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO ui_message_registry (user_id, slot, message_ids_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, slot) DO UPDATE SET
                message_ids_json = excluded.message_ids_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(user_id), str(slot), json.dumps(normalized)),
        )
        await db.commit()
        return normalized
    finally:
        await db.close()


async def replace_ui_message_bundle(
    bot,
    *,
    chat_id: int,
    new_message_ids: Iterable[int],
    slot: str = PRIMARY_UI_SLOT,
) -> list[int]:
    """Оставляет в слоте только новый комплект служебных сообщений.

    Новый комплект сначала регистрируется, после чего удаляется предыдущий. Если Telegram
    уже не разрешает удалить старое сообщение, это не ломает дальнейшую работу реестра.
    """
    normalized = _normalize_message_ids(new_message_ids)
    async with _slot_lock(chat_id, slot):
        old_ids = await get_ui_message_ids(chat_id, slot)
        await set_ui_message_ids(chat_id, slot, normalized)
        new_set = set(normalized)
        await delete_message_ids(bot, chat_id, (item for item in old_ids if item not in new_set))
    return normalized


async def clear_ui_message_bundle(
    bot,
    *,
    chat_id: int,
    slot: str = PRIMARY_UI_SLOT,
) -> None:
    async with _slot_lock(chat_id, slot):
        old_ids = await get_ui_message_ids(chat_id, slot)
        await set_ui_message_ids(chat_id, slot, [])
        await delete_message_ids(bot, chat_id, old_ids)


async def send_ui_parts(
    bot,
    *,
    chat_id: int,
    parts: Sequence[UiMessagePart],
    slot: str = PRIMARY_UI_SLOT,
) -> list[int]:
    sent_ids: list[int] = []
    try:
        for part in parts:
            message = await bot.send_message(
                chat_id=int(chat_id),
                text=str(part.text),
                reply_markup=part.reply_markup,
            )
            sent_ids.append(int(message.message_id))
    except Exception:
        await delete_message_ids(bot, chat_id, sent_ids)
        raise

    try:
        await replace_ui_message_bundle(
            bot,
            chat_id=int(chat_id),
            new_message_ids=sent_ids,
            slot=slot,
        )
    except Exception:
        await delete_message_ids(bot, chat_id, sent_ids)
        raise
    return sent_ids


async def send_ui_text(
    bot,
    *,
    chat_id: int,
    text: str,
    reply_markup=None,
    slot: str = PRIMARY_UI_SLOT,
) -> list[int]:
    chunks = split_ui_text(text)
    parts = [
        UiMessagePart(
            text=(f"Часть {index + 1}/{len(chunks)}\n\n" if len(chunks) > 1 else "") + chunk,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
        )
        for index, chunk in enumerate(chunks)
    ]
    return await send_ui_parts(bot, chat_id=chat_id, parts=parts, slot=slot)


async def delete_trigger_message(message) -> None:
    """Убирает из личного чата сообщение-команду от reply-кнопки, если Telegram разрешает."""
    try:
        await message.bot.delete_message(
            chat_id=int(message.chat.id),
            message_id=int(message.message_id),
        )
    except Exception:
        logger.debug(
            "Не удалось удалить сообщение-триггер %s в чате %s",
            getattr(message, "message_id", None),
            getattr(getattr(message, "chat", None), "id", None),
            exc_info=True,
        )
