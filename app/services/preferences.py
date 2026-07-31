from __future__ import annotations

from app.database import get_db
from app.presentation.texts import FRIENDLY, STRICT, normalize_style, text_for_style
from app.services.ui_versions import friendly_style_enabled


async def get_message_style(telegram_id: int) -> str:
    if not friendly_style_enabled():
        return STRICT

    db = await get_db()
    cursor = await db.execute(
        "SELECT message_style FROM users WHERE telegram_id = ?",
        (int(telegram_id),),
    )
    row = await cursor.fetchone()
    await db.close()
    if not row:
        return STRICT
    return normalize_style(row["message_style"])


async def set_message_style(telegram_id: int, style: str) -> str:
    selected = normalize_style(style)
    if selected == FRIENDLY and not friendly_style_enabled():
        selected = STRICT

    db = await get_db()
    await db.execute(
        """
        UPDATE users
        SET message_style = ?, updated_at = CURRENT_TIMESTAMP
        WHERE telegram_id = ?
        """,
        (selected, int(telegram_id)),
    )
    await db.commit()
    await db.close()
    return selected


async def user_text(telegram_id: int, key: str, **values) -> str:
    return text_for_style(await get_message_style(telegram_id), key, **values)
