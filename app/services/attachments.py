from app.database import get_db


async def create_attachment(
    ticket_id: int,
    file_id: str,
    file_type: str,
    file_name: str | None = None,
    user_id: int | None = None,
    file_unique_id: str | None = None,
    caption: str | None = None,
    uploaded_by: int | None = None,
) -> int:
    if user_id is None and uploaded_by is not None:
        user_id = uploaded_by

    db = await get_db()

    cursor = await db.execute(
        """
        INSERT INTO ticket_attachments (
            ticket_id,
            user_id,
            file_id,
            file_unique_id,
            file_type,
            file_name,
            caption
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticket_id,
            user_id,
            file_id,
            file_unique_id,
            file_type,
            file_name,
            caption
        )
    )

    await db.commit()
    attachment_id = cursor.lastrowid
    await db.close()

    return attachment_id


async def get_ticket_attachments(ticket_id: int):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM ticket_attachments
        WHERE ticket_id = ?
        ORDER BY created_at ASC
        """,
        (ticket_id,)
    )

    attachments = await cursor.fetchall()
    await db.close()

    return attachments