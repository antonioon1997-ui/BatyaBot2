from __future__ import annotations

import json

from app.database import get_db

POLL_TYPES = {"choice", "rating"}
POLL_STATUSES = {"active", "closed"}


def parse_poll_options(value: str | None) -> list[str]:
    try:
        loaded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item).strip() for item in loaded if str(item).strip()]


async def create_poll(
    *,
    poll_type: str,
    question: str,
    options: list[str],
    none_label: str,
    created_by: int,
) -> int:
    if poll_type not in POLL_TYPES:
        raise ValueError("Неизвестный тип голосования")
    clean_question = str(question or "").strip()
    clean_options = [str(item).strip() for item in options if str(item).strip()]
    clean_none = str(none_label or "").strip()
    if not clean_question:
        raise ValueError("Вопрос голосования пуст")
    if poll_type == "choice" and not 2 <= len(clean_options) <= 5:
        raise ValueError("Для выбора нужно от 2 до 5 вариантов")
    if poll_type == "rating":
        clean_options = ["1", "2", "3", "4", "5"]
    if not clean_none:
        raise ValueError("Нужен вариант отказа")

    db = await get_db()
    cursor = await db.execute(
        """
        INSERT INTO polls (
            poll_type, question, options_json, none_label,
            status, created_by
        )
        VALUES (?, ?, ?, ?, 'active', ?)
        """,
        (
            poll_type,
            clean_question,
            json.dumps(clean_options, ensure_ascii=False),
            clean_none,
            int(created_by),
        ),
    )
    await db.commit()
    poll_id = int(cursor.lastrowid)
    await db.close()
    return poll_id


async def get_poll(poll_id: int):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM polls WHERE id = ?", (int(poll_id),))
    row = await cursor.fetchone()
    await db.close()
    return row


async def list_polls(*, status: str | None = None, limit: int = 30):
    db = await get_db()
    if status:
        cursor = await db.execute(
            """
            SELECT * FROM polls
            WHERE status = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (status, int(limit)),
        )
    else:
        cursor = await db.execute(
            """
            SELECT * FROM polls
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
    rows = await cursor.fetchall()
    await db.close()
    return rows


async def close_poll(poll_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        """
        UPDATE polls
        SET status = 'closed', closed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'active'
        """,
        (int(poll_id),),
    )
    await db.commit()
    changed = cursor.rowcount > 0
    await db.close()
    return changed


async def get_user_vote(poll_id: int, user_id: int) -> str | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT choice_key FROM poll_votes WHERE poll_id = ? AND user_id = ?",
        (int(poll_id), int(user_id)),
    )
    row = await cursor.fetchone()
    await db.close()
    return str(row["choice_key"]) if row else None


async def upsert_vote(poll_id: int, user_id: int, choice_key: str) -> bool:
    poll = await get_poll(poll_id)
    if not poll or poll["status"] != "active":
        return False

    options = parse_poll_options(poll["options_json"])
    valid_keys = {str(index) for index in range(len(options))} | {"none"}
    if choice_key not in valid_keys:
        return False

    db = await get_db()
    await db.execute(
        """
        INSERT INTO poll_votes (poll_id, user_id, choice_key)
        VALUES (?, ?, ?)
        ON CONFLICT(poll_id, user_id) DO UPDATE SET
            choice_key = excluded.choice_key,
            updated_at = CURRENT_TIMESTAMP
        """,
        (int(poll_id), int(user_id), choice_key),
    )
    await db.commit()
    await db.close()
    return True


async def get_poll_results(poll_id: int) -> dict:
    poll = await get_poll(poll_id)
    if not poll:
        return {"total": 0, "counts": {}, "poll": None}

    db = await get_db()
    cursor = await db.execute(
        """
        SELECT choice_key, COUNT(*) AS total
        FROM poll_votes
        WHERE poll_id = ?
        GROUP BY choice_key
        """,
        (int(poll_id),),
    )
    rows = await cursor.fetchall()
    await db.close()
    counts = {str(row["choice_key"]): int(row["total"] or 0) for row in rows}
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "poll": poll,
    }
