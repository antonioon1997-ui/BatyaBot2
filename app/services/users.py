from app.database import get_db
from app.config import settings
from app.domain import CLIENT_ROLE_ALIASES, PURCHASING_ROLE_ALIASES, normalize_department


async def log_admin_action(
    admin_telegram_id: int,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: str | None = None,
):
    db = await get_db()

    await db.execute(
        """
        INSERT INTO admin_actions (
            admin_telegram_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            admin_telegram_id,
            action,
            entity_type,
            entity_id,
            details,
        )
    )

    await db.commit()
    await db.close()


async def get_user_by_telegram_id(telegram_id: int):
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )
    user = await cursor.fetchone()
    await db.close()
    return user


async def get_users_by_role(role: str):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM users
        WHERE role = ?
          AND is_active = 1
        ORDER BY created_at DESC
        """,
        (role,)
    )

    users = await cursor.fetchall()
    await db.close()

    return users


async def get_users_by_department(department: str):
    normalized = normalize_department(department)
    if normalized == "purchasing":
        roles = tuple(PURCHASING_ROLE_ALIASES)
    elif normalized == "client":
        roles = tuple(CLIENT_ROLE_ALIASES)
    else:
        return []

    db = await get_db()
    placeholders = ", ".join("?" for _ in roles)
    cursor = await db.execute(
        f"SELECT * FROM users WHERE is_active = 1 AND role IN ({placeholders}) ORDER BY created_at DESC",
        roles,
    )
    users = await cursor.fetchall()
    await db.close()
    return users


async def create_or_update_access_request(telegram_id: int, username: str | None, full_name: str):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM access_requests
        WHERE telegram_id = ? AND status = 'new'
        """,
        (telegram_id,)
    )
    existing = await cursor.fetchone()

    if existing:
        await db.close()
        return existing

    await db.execute(
        """
        INSERT INTO access_requests (telegram_id, username, full_name)
        VALUES (?, ?, ?)
        """,
        (telegram_id, username, full_name)
    )

    await db.commit()

    cursor = await db.execute(
        """
        SELECT *
        FROM access_requests
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (telegram_id,)
    )
    request = await cursor.fetchone()

    await db.close()
    return request


async def approve_user(telegram_id: int, role: str, admin_telegram_id: int):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM access_requests
        WHERE telegram_id = ? AND status = 'new'
        ORDER BY id DESC
        LIMIT 1
        """,
        (telegram_id,)
    )
    request = await cursor.fetchone()

    username = request["username"] if request else None
    full_name = request["full_name"] if request else "Без имени"

    await db.execute(
        """
        INSERT INTO users (telegram_id, username, full_name, role, is_active, updated_at)
        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(telegram_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            role = excluded.role,
            is_active = 1,
            updated_at = CURRENT_TIMESTAMP,
            restored_at = CURRENT_TIMESTAMP,
            restored_by = ?
        """,
        (telegram_id, username, full_name, role, admin_telegram_id)
    )

    await db.execute(
        """
        UPDATE access_requests
        SET status = 'approved',
            processed_at = CURRENT_TIMESTAMP,
            processed_by = ?
        WHERE telegram_id = ? AND status = 'new'
        """,
        (admin_telegram_id, telegram_id)
    )

    await db.execute(
        """
        INSERT INTO admin_actions (
            admin_telegram_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            admin_telegram_id,
            "approve_user",
            "user",
            telegram_id,
            f"Выдана роль: {role}"
        )
    )

    await db.commit()
    await db.close()


async def reject_user(telegram_id: int, admin_telegram_id: int):
    db = await get_db()

    await db.execute(
        """
        UPDATE access_requests
        SET status = 'rejected',
            processed_at = CURRENT_TIMESTAMP,
            processed_by = ?
        WHERE telegram_id = ? AND status = 'new'
        """,
        (admin_telegram_id, telegram_id)
    )

    await db.execute(
        """
        INSERT INTO admin_actions (
            admin_telegram_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            admin_telegram_id,
            "reject_user",
            "user_request",
            telegram_id,
            "Заявка на доступ отклонена"
        )
    )

    await db.commit()
    await db.close()


async def get_access_requests(status: str | None = None, limit: int = 20):
    db = await get_db()

    if status:
        cursor = await db.execute(
            """
            SELECT *
            FROM access_requests
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (status, limit)
        )
    else:
        cursor = await db.execute(
            """
            SELECT *
            FROM access_requests
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )

    requests = await cursor.fetchall()
    await db.close()

    return requests


async def get_access_request_by_id(request_id: int):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM access_requests
        WHERE id = ?
        """,
        (request_id,)
    )

    request = await cursor.fetchone()
    await db.close()

    return request


async def get_latest_access_request_by_telegram_id(telegram_id: int):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM access_requests
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (telegram_id,)
    )

    request = await cursor.fetchone()
    await db.close()

    return request


async def get_active_users():
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM users
        WHERE is_active = 1
        ORDER BY created_at DESC
        """
    )

    users = await cursor.fetchall()
    await db.close()
    return users


async def get_inactive_users():
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM users
        WHERE is_active = 0
        ORDER BY updated_at DESC, created_at DESC
        """
    )

    users = await cursor.fetchall()
    await db.close()
    return users


async def get_all_users(limit: int = 50):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM users
        ORDER BY is_active DESC, created_at DESC
        LIMIT ?
        """,
        (limit,)
    )

    users = await cursor.fetchall()
    await db.close()

    return users


async def get_users_by_status(is_active: int, limit: int = 50):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT *
        FROM users
        WHERE is_active = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (is_active, limit)
    )

    users = await cursor.fetchall()
    await db.close()

    return users


async def deactivate_user(telegram_id: int, admin_telegram_id: int):
    db = await get_db()

    await db.execute(
        """
        UPDATE users
        SET is_active = 0,
            updated_at = CURRENT_TIMESTAMP,
            deactivated_at = CURRENT_TIMESTAMP,
            deactivated_by = ?
        WHERE telegram_id = ?
        """,
        (admin_telegram_id, telegram_id)
    )

    await db.execute(
        """
        INSERT INTO admin_actions (
            admin_telegram_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            admin_telegram_id,
            "deactivate_user",
            "user",
            telegram_id,
            "Пользователь удалён мягко: is_active = 0"
        )
    )

    await db.commit()
    await db.close()


async def restore_user(telegram_id: int, admin_telegram_id: int):
    db = await get_db()

    await db.execute(
        """
        UPDATE users
        SET is_active = 1,
            updated_at = CURRENT_TIMESTAMP,
            restored_at = CURRENT_TIMESTAMP,
            restored_by = ?
        WHERE telegram_id = ?
        """,
        (admin_telegram_id, telegram_id)
    )

    await db.execute(
        """
        INSERT INTO admin_actions (
            admin_telegram_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            admin_telegram_id,
            "restore_user",
            "user",
            telegram_id,
            "Пользователь восстановлен: is_active = 1"
        )
    )

    await db.commit()
    await db.close()


async def set_user_role(telegram_id: int, role: str, admin_telegram_id: int):
    db = await get_db()

    await db.execute(
        """
        UPDATE users
        SET role = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE telegram_id = ?
        """,
        (role, telegram_id)
    )

    await db.execute(
        """
        INSERT INTO admin_actions (
            admin_telegram_id,
            action,
            entity_type,
            entity_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            admin_telegram_id,
            "set_user_role",
            "user",
            telegram_id,
            f"Новая роль: {role}"
        )
    )

    await db.commit()
    await db.close()


async def get_user_tickets_summary(telegram_id: int):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) AS visible_total,
            SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) AS deleted_total,
            SUM(CASE WHEN is_deleted = 0 AND status IN ('new', 'in_work', 'waiting_answer', 'waiting_confirmation') THEN 1 ELSE 0 END) AS open_total,
            SUM(CASE WHEN is_deleted = 0 AND status IN ('done', 'cancelled') THEN 1 ELSE 0 END) AS closed_total
        FROM tickets
        WHERE created_by = ?
        """,
        (telegram_id,)
    )

    summary = await cursor.fetchone()
    await db.close()

    return summary


async def is_admin(telegram_id: int) -> bool:
    """Единая проверка администратора по ADMIN_ID из .env."""
    from app.config import settings

    try:
        return int(telegram_id) == int(settings.admin_id)
    except (TypeError, ValueError):
        return False
