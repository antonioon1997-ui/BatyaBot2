from __future__ import annotations

from app.database import get_db
from app.domain import normalize_department


async def get_response_templates(department: str = "purchasing", *, include_inactive: bool = False):
    department = normalize_department(department) or department
    db = await get_db()
    try:
        where = "department = ?"
        params: list = [department]
        if not include_inactive:
            where += " AND is_active = 1"
        cursor = await db.execute(
            f"SELECT * FROM response_templates WHERE {where} ORDER BY is_active DESC, id",
            tuple(params),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_response_template(template_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM response_templates WHERE id = ?", (template_id,))
        return await cursor.fetchone()
    finally:
        await db.close()


async def create_response_template(title: str, body: str, created_by: int, department: str = "purchasing") -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO response_templates(department,title,body,created_by) VALUES(?,?,?,?)",
            (normalize_department(department) or department, title.strip(), body.strip(), created_by),
        )
        await db.commit()
        return int(cursor.lastrowid)
    finally:
        await db.close()


async def update_response_template(template_id: int, *, title: str | None = None, body: str | None = None, is_active: bool | None = None) -> bool:
    updates = []
    params: list = []
    if title is not None:
        updates.append("title = ?")
        params.append(title.strip())
    if body is not None:
        updates.append("body = ?")
        params.append(body.strip())
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if is_active else 0)
    if not updates:
        return False
    updates.extend(["updated_at = CURRENT_TIMESTAMP"])
    params.append(template_id)
    db = await get_db()
    try:
        cursor = await db.execute(
            f"UPDATE response_templates SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
