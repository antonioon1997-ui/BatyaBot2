from __future__ import annotations

from app.database import get_db


async def create_feedback(
    *,
    user_id: int,
    username: str | None,
    full_name: str | None,
    role: str | None,
    source: str,
    text: str | None,
    file_id: str | None = None,
    file_type: str | None = None,
    file_name: str | None = None,
) -> int:
    db = await get_db()
    cursor = await db.execute(
        """
        INSERT INTO feedback_messages (
            user_id, username, full_name, role, source, text,
            file_id, file_type, file_name, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
        """,
        (
            int(user_id),
            username,
            full_name,
            role,
            source,
            text,
            file_id,
            file_type,
            file_name,
        ),
    )
    await db.commit()
    feedback_id = int(cursor.lastrowid)
    await db.close()
    return feedback_id


async def get_feedback(feedback_id: int):
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM feedback_messages WHERE id = ?",
        (int(feedback_id),),
    )
    row = await cursor.fetchone()
    await db.close()
    return row


async def list_feedback(*, status: str | None = None, limit: int = 30):
    db = await get_db()
    if status:
        cursor = await db.execute(
            """
            SELECT * FROM feedback_messages
            WHERE status = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (status, int(limit)),
        )
    else:
        cursor = await db.execute(
            """
            SELECT * FROM feedback_messages
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
    rows = await cursor.fetchall()
    await db.close()
    return rows


async def set_feedback_status(feedback_id: int, status: str) -> bool:
    if status not in {"new", "in_work", "done"}:
        raise ValueError("Неизвестный статус сообщения")

    db = await get_db()
    cursor = await db.execute(
        """
        UPDATE feedback_messages
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, int(feedback_id)),
    )
    await db.commit()
    changed = cursor.rowcount > 0
    await db.close()
    return changed


async def count_feedback_by_status() -> dict[str, int]:
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT status, COUNT(*) AS total
        FROM feedback_messages
        GROUP BY status
        """
    )
    rows = await cursor.fetchall()
    await db.close()
    result = {"new": 0, "in_work": 0, "done": 0}
    for row in rows:
        result[str(row["status"])] = int(row["total"] or 0)
    return result
