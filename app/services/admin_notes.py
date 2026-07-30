from __future__ import annotations

from app.database import get_db


async def list_notes():
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM admin_notes ORDER BY CASE status WHEN 'planned' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, id DESC"
        )
        return await cur.fetchall()
    finally:
        await db.close()


async def get_note(note_id: int):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM admin_notes WHERE id = ?", (note_id,))
        return await cur.fetchone()
    finally:
        await db.close()


async def create_note(title: str, body: str, created_by: int) -> int:
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO admin_notes(title, body, created_by) VALUES(?,?,?)",
            (title.strip(), body.strip(), int(created_by)),
        )
        await db.commit()
        return int(cur.lastrowid)
    finally:
        await db.close()


async def update_note(note_id: int, *, title: str | None = None, body: str | None = None, status: str | None = None) -> bool:
    updates, params = [], []
    if title is not None:
        updates.append("title = ?")
        params.append(title.strip())
    if body is not None:
        updates.append("body = ?")
        params.append(body.strip())
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if not updates:
        return False
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(int(note_id))
    db = await get_db()
    try:
        cur = await db.execute(f"UPDATE admin_notes SET {', '.join(updates)} WHERE id = ?", params)
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def delete_note(note_id: int) -> bool:
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM admin_notes WHERE id = ?", (int(note_id),))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()
