from app.database import get_db
from app.domain import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    CLOSED_STATUSES,
    CLIENT_ROLE_ALIASES,
    DEPARTMENT_CLIENT,
    DEPARTMENT_PURCHASING,
    OPEN_STATUSES,
    PURCHASING_ROLE_ALIASES,
    STATUS_IN_WORK,
    STATUS_NEW,
    STATUS_WAITING_CONFIRMATION,
    normalize_department,
    opposite_department,
)


AUTO_CLOSE_MINUTES = 10


async def _insert_ticket_event(
    db,
    ticket_id: int,
    event_type: str,
    actor_telegram_id: int | None = None,
    details: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO ticket_events (ticket_id, actor_telegram_id, event_type, details)
        VALUES (?, ?, ?, ?)
        """,
        (ticket_id, actor_telegram_id, event_type, details),
    )


async def get_user_department(telegram_id: int) -> str:
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT role
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )

    user = await cursor.fetchone()
    await db.close()

    if not user:
        return "unknown"

    role = user["role"]

    department = normalize_department(role)

    if department in {DEPARTMENT_CLIENT, DEPARTMENT_PURCHASING}:
        return department

    return "unknown"


async def add_ticket_event(
    ticket_id: int,
    event_type: str,
    actor_telegram_id: int | None = None,
    details: str | None = None,
):
    db = await get_db()
    try:
        await _insert_ticket_event(
            db,
            ticket_id,
            event_type,
            actor_telegram_id,
            details,
        )
        await db.commit()
    finally:
        await db.close()


async def get_ticket_events(ticket_id: int, limit: int = 100):
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT e.*, u.full_name AS actor_name, u.username AS actor_username
        FROM ticket_events e
        LEFT JOIN users u ON u.telegram_id = e.actor_telegram_id
        WHERE e.ticket_id = ?
        ORDER BY e.created_at ASC, e.id ASC
        LIMIT ?
        """,
        (ticket_id, limit),
    )
    rows = await cursor.fetchall()
    await db.close()
    return rows


async def create_ticket(
    title: str,
    description: str,
    order_number: str | None,
    created_by: int,
    executor_department: str,
    requester_department: str | None = None,
    ticket_type: str = "task",
    direction: str | None = None,
    order_status_snapshot: str | None = None,
):
    if requester_department is None:
        requester_department = await get_user_department(created_by)

    requester_department = normalize_department(requester_department) or "unknown"
    executor_department = normalize_department(executor_department) or opposite_department(requester_department)

    if direction is None:
        direction = f"{requester_department}_to_{executor_department}"

    db = await get_db()
    try:
        cursor = await db.execute(
            """
            INSERT INTO tickets (
                title,
                description,
                order_number,
                order_status_snapshot,
                ticket_type,
                direction,
                status,
                created_by,
                requester_department,
                executor_department,
                is_deleted,
                excluded_from_stats,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                title,
                description,
                order_number,
                order_status_snapshot,
                ticket_type,
                direction,
                created_by,
                requester_department,
                executor_department,
            ),
        )
        ticket_id = int(cursor.lastrowid)
        await _insert_ticket_event(ticket_id=ticket_id, db=db, event_type="created", actor_telegram_id=created_by, details="Тикет создан")
        await db.execute(
            "INSERT INTO ticket_metrics(ticket_id, updated_at) VALUES (?, CURRENT_TIMESTAMP) ON CONFLICT(ticket_id) DO NOTHING",
            (ticket_id,),
        )
        await db.commit()
        return ticket_id
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_ticket_by_id(ticket_id: int):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username,
            a.full_name AS assignee_full_name,
            a.username AS assignee_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        LEFT JOIN users a ON a.telegram_id = t.taken_by
        WHERE t.id = ?
          AND t.is_deleted = 0
        """,
        (ticket_id,)
    )

    ticket = await cursor.fetchone()
    await db.close()

    return ticket


async def get_ticket_by_id_admin(ticket_id: int):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username,
            a.full_name AS assignee_full_name,
            a.username AS assignee_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        LEFT JOIN users a ON a.telegram_id = t.taken_by
        WHERE t.id = ?
        """,
        (ticket_id,)
    )

    ticket = await cursor.fetchone()
    await db.close()

    return ticket


async def get_outgoing_tickets(telegram_id: int, limit: int = 30):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        WHERE t.is_deleted = 0
          AND t.created_by = ?
          AND t.status NOT IN ('done', 'cancelled')
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (telegram_id, limit)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def get_my_tickets(telegram_id: int, limit: int = 20):
    return await get_outgoing_tickets(telegram_id=telegram_id, limit=limit)


async def get_incoming_tickets(
    department: str | None = None,
    executor_department: str | None = None,
    limit: int | None = 30,
):
    db = await get_db()

    selected_department = executor_department if executor_department is not None else department
    selected_department = normalize_department(selected_department)

    params = []

    where_sql = """
        WHERE t.is_deleted = 0
          AND t.status NOT IN ('done', 'cancelled')
          AND (t.snoozed_until IS NULL OR t.snoozed_until <= CURRENT_TIMESTAMP)
    """

    if selected_department:
        where_sql += """
          AND t.executor_department = ?
        """
        params.append(selected_department)

    limit_sql = ""

    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)

    cursor = await db.execute(
        f"""
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        {where_sql}
        ORDER BY t.created_at DESC
        {limit_sql}
        """,
        tuple(params)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def get_work_tickets(
    telegram_id: int | None = None,
    department: str | None = None,
    limit: int = 30,
):
    db = await get_db()

    params = []

    where_sql = """
        WHERE t.is_deleted = 0
          AND t.status = 'in_work'
          AND (t.snoozed_until IS NULL OR t.snoozed_until <= CURRENT_TIMESTAMP)
    """

    if telegram_id is not None:
        where_sql += """
          AND (
                t.taken_by = ?
                OR t.created_by = ?
          )
        """
        params.extend([telegram_id, telegram_id])

    normalized_department = normalize_department(department)

    if normalized_department:
        where_sql += """
          AND t.executor_department = ?
        """
        params.append(normalized_department)

    params.append(limit)

    cursor = await db.execute(
        f"""
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        {where_sql}
        ORDER BY COALESCE(t.updated_at, t.created_at) DESC
        LIMIT ?
        """,
        tuple(params)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def get_archive_incoming_tickets(department: str | None = None, limit: int | None = 50):
    db = await get_db()

    params = []

    where_sql = """
        WHERE t.is_deleted = 0
          AND t.status IN ('done', 'cancelled')
    """

    normalized_department = normalize_department(department)

    if normalized_department:
        where_sql += """
          AND t.executor_department = ?
        """
        params.append(normalized_department)

    limit_sql = ""

    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)

    cursor = await db.execute(
        f"""
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        {where_sql}
        ORDER BY COALESCE(t.closed_at, t.updated_at, t.created_at) DESC
        {limit_sql}
        """,
        tuple(params)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def get_archive_outgoing_tickets(telegram_id: int | None = None, limit: int | None = 50):
    db = await get_db()

    params = []

    where_sql = """
        WHERE t.is_deleted = 0
          AND t.status IN ('done', 'cancelled')
    """

    if telegram_id is not None:
        where_sql += """
          AND t.created_by = ?
        """
        params.append(telegram_id)

    limit_sql = ""

    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)

    cursor = await db.execute(
        f"""
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        {where_sql}
        ORDER BY COALESCE(t.closed_at, t.updated_at, t.created_at) DESC
        {limit_sql}
        """,
        tuple(params)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets



async def add_ticket_comment(
    ticket_id: int,
    author_telegram_id: int,
    text: str,
    *,
    cancel_auto_close: bool = False,
    start_work_if_new: bool = False,
) -> bool:
    """Добавляет комментарий; может начать работу без обязательного назначения исполнителя."""
    db = await get_db()
    auto_close_cancelled = False
    try:
        await db.execute(
            """
            INSERT INTO ticket_comments (ticket_id, user_id, comment, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (ticket_id, author_telegram_id, text),
        )

        started_work = False
        if start_work_if_new:
            cursor = await db.execute(
                """
                UPDATE tickets
                SET status='in_work', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND is_deleted=0 AND status='new'
                """,
                (ticket_id,),
            )
            started_work = cursor.rowcount > 0

        if cancel_auto_close:
            cursor = await db.execute(
                """
                UPDATE tickets
                SET status = 'in_work',
                    auto_close_at = NULL,
                    closed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND is_deleted = 0
                  AND status = 'waiting_confirmation'
                  AND auto_close_at IS NOT NULL
                """,
                (ticket_id,),
            )
            auto_close_cancelled = cursor.rowcount > 0

        if not auto_close_cancelled:
            await db.execute(
                """
                UPDATE tickets
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND is_deleted = 0
                """,
                (ticket_id,),
            )

        await _insert_ticket_event(
            db,
            ticket_id,
            "comment",
            author_telegram_id,
            "Добавлен комментарий",
        )
        if started_work:
            await _insert_ticket_event(
                db,
                ticket_id,
                "status_changed",
                author_telegram_id,
                "Статус изменён на: in_work; исполнитель не назначен",
            )
        if auto_close_cancelled:
            await _insert_ticket_event(
                db,
                ticket_id,
                "auto_close_cancelled",
                author_telegram_id,
                "Автоматическое закрытие отменено новым комментарием",
            )
        cursor = await db.execute(
            """
            SELECT t.executor_department, u.role
            FROM tickets t LEFT JOIN users u ON u.telegram_id = ?
            WHERE t.id = ?
            """,
            (author_telegram_id, ticket_id),
        )
        metric_context = await cursor.fetchone()
        if metric_context and normalize_department(metric_context["role"]) == normalize_department(metric_context["executor_department"]):
            await db.execute(
                """
                INSERT INTO ticket_metrics(ticket_id, first_response_at, updated_at)
                VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    first_response_at = COALESCE(ticket_metrics.first_response_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (ticket_id,),
            )
        await db.commit()
        return auto_close_cancelled
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

async def get_ticket_comments(ticket_id: int, limit: int = 20):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            c.id,
            c.ticket_id,
            c.user_id AS author_telegram_id,
            c.comment AS text,
            c.created_at,
            u.full_name AS author_name,
            u.username AS author_username
        FROM ticket_comments c
        LEFT JOIN users u ON u.telegram_id = c.user_id
        WHERE c.ticket_id = ?
        ORDER BY c.created_at ASC
        LIMIT ?
        """,
        (ticket_id, limit)
    )

    comments = await cursor.fetchall()
    await db.close()

    return comments



async def update_ticket_status(
    ticket_id: int,
    status: str,
    *,
    actor_telegram_id: int | None = None,
    comment: str | None = None,
    expected_statuses: tuple[str, ...] | None = None,
    require_auto_close: bool | None = None,
) -> bool:
    """Атомарно меняет статус, добавляет комментарий и событие истории."""
    if comment and actor_telegram_id is None:
        raise ValueError("Для системного комментария нужен actor_telegram_id")

    conditions = ["id = ?", "is_deleted = 0"]
    params: list = []

    if status in CLOSED_STATUSES:
        set_sql = """
            status = ?,
            closed_at = CURRENT_TIMESTAMP,
            auto_close_at = NULL,
            snoozed_until = NULL,
            snoozed_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        """
    elif status == STATUS_IN_WORK:
        set_sql = """
            status = ?,
            reopened_at = CASE WHEN closed_at IS NOT NULL THEN CURRENT_TIMESTAMP ELSE reopened_at END,
            closed_at = NULL,
            auto_close_at = NULL,
            snoozed_until = NULL,
            snoozed_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        """
    else:
        set_sql = """
            status = ?,
            auto_close_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        """

    params.append(status)
    params.append(ticket_id)

    if expected_statuses:
        placeholders = ", ".join("?" for _ in expected_statuses)
        conditions.append(f"status IN ({placeholders})")
        params.extend(expected_statuses)
    if require_auto_close is True:
        conditions.append("auto_close_at IS NOT NULL")
    elif require_auto_close is False:
        conditions.append("auto_close_at IS NULL")

    db = await get_db()
    try:
        cursor = await db.execute(
            f"UPDATE tickets SET {set_sql} WHERE {' AND '.join(conditions)}",
            tuple(params),
        )
        changed = cursor.rowcount > 0
        if not changed:
            await db.rollback()
            return False

        if comment:
            await db.execute(
                """
                INSERT INTO ticket_comments (ticket_id, user_id, comment, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (ticket_id, actor_telegram_id or 0, comment),
            )
            await _insert_ticket_event(
                db,
                ticket_id,
                "comment",
                actor_telegram_id,
                "Добавлен комментарий",
            )

        await _insert_ticket_event(
            db,
            ticket_id,
            "status_changed",
            actor_telegram_id,
            f"Статус изменён на: {status}",
        )
        if status == STATUS_WAITING_CONFIRMATION:
            await db.execute(
                """
                INSERT INTO ticket_metrics(ticket_id, first_response_at, first_completed_at, updated_at)
                VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    first_response_at = COALESCE(ticket_metrics.first_response_at, CURRENT_TIMESTAMP),
                    first_completed_at = COALESCE(ticket_metrics.first_completed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (ticket_id,),
            )
        elif status == 'done':
            await db.execute(
                """
                INSERT INTO ticket_metrics(ticket_id, first_completed_at, updated_at)
                VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    first_completed_at = COALESCE(ticket_metrics.first_completed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (ticket_id,),
            )
        elif status == STATUS_IN_WORK:
            await db.execute(
                """
                INSERT INTO ticket_metrics(ticket_id, reopen_count, updated_at)
                VALUES (?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    reopen_count = ticket_metrics.reopen_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (ticket_id,),
            )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def schedule_ticket_auto_close(
    ticket_id: int,
    actor_telegram_id: int | None = None,
    minutes: int = AUTO_CLOSE_MINUTES,
    *,
    comment: str | None = None,
) -> bool:
    if comment and actor_telegram_id is None:
        raise ValueError("Для системного комментария нужен actor_telegram_id")

    safe_minutes = max(1, int(minutes))
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = 'waiting_confirmation',
                auto_close_at = DATETIME('now', ?),
                closed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND is_deleted = 0
              AND requester_department = 'client'
              AND executor_department = 'purchasing'
              AND status IN ('new', 'in_work')
            """,
            (f"+{safe_minutes} minutes", ticket_id),
        )
        changed = cursor.rowcount > 0
        if not changed:
            await db.rollback()
            return False

        if comment:
            await db.execute(
                """
                INSERT INTO ticket_comments (ticket_id, user_id, comment, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (ticket_id, actor_telegram_id or 0, comment),
            )
            await _insert_ticket_event(db, ticket_id, "comment", actor_telegram_id, "Добавлен комментарий")

        await _insert_ticket_event(
            db,
            ticket_id,
            "auto_close_scheduled",
            actor_telegram_id,
            f"Автоматическое закрытие назначено через {safe_minutes} минут",
        )
        await db.execute(
            """
            INSERT INTO ticket_metrics(ticket_id, first_response_at, first_completed_at, updated_at)
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id) DO UPDATE SET
                first_response_at = COALESCE(ticket_metrics.first_response_at, CURRENT_TIMESTAMP),
                first_completed_at = COALESCE(ticket_metrics.first_completed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            """,
            (ticket_id,),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def cancel_ticket_auto_close(
    ticket_id: int,
    actor_telegram_id: int | None = None,
    reason: str = "Автоматическое закрытие отменено",
    *,
    comment: str | None = None,
) -> bool:
    if comment and actor_telegram_id is None:
        raise ValueError("Для системного комментария нужен actor_telegram_id")

    db = await get_db()
    try:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = 'in_work',
                auto_close_at = NULL,
                closed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND is_deleted = 0
              AND status = 'waiting_confirmation'
              AND auto_close_at IS NOT NULL
            """,
            (ticket_id,),
        )
        changed = cursor.rowcount > 0
        if not changed:
            await db.rollback()
            return False

        if comment:
            await db.execute(
                """
                INSERT INTO ticket_comments (ticket_id, user_id, comment, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (ticket_id, actor_telegram_id or 0, comment),
            )
            await _insert_ticket_event(db, ticket_id, "comment", actor_telegram_id, "Добавлен комментарий")
        await _insert_ticket_event(db, ticket_id, "auto_close_cancelled", actor_telegram_id, reason)
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

async def get_due_auto_close_tickets():
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT t.*
        FROM tickets t
        WHERE t.is_deleted = 0
          AND t.status = 'waiting_confirmation'
          AND t.requester_department = 'client'
          AND t.executor_department = 'purchasing'
          AND t.auto_close_at IS NOT NULL
          AND DATETIME(t.auto_close_at) <= CURRENT_TIMESTAMP
        ORDER BY DATETIME(t.auto_close_at) ASC, t.id ASC
        """
    )
    tickets = await cursor.fetchall()
    await db.close()
    return tickets



async def close_due_auto_close_ticket(ticket_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = 'done',
                closed_at = CURRENT_TIMESTAMP,
                auto_close_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND is_deleted = 0
              AND status = 'waiting_confirmation'
              AND requester_department = 'client'
              AND executor_department = 'purchasing'
              AND auto_close_at IS NOT NULL
              AND DATETIME(auto_close_at) <= CURRENT_TIMESTAMP
            """,
            (ticket_id,),
        )
        changed = cursor.rowcount > 0
        if not changed:
            await db.rollback()
            return False
        await _insert_ticket_event(
            db,
            ticket_id,
            "auto_closed",
            None,
            "Тикет автоматически закрыт после ожидания подтверждения",
        )
        await db.execute(
            """
            INSERT INTO ticket_metrics(ticket_id, first_completed_at, updated_at)
            VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id) DO UPDATE SET
                first_completed_at = COALESCE(ticket_metrics.first_completed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            """,
            (ticket_id,),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

async def count_tickets_for_department_reminder(department: str, category: str) -> int:
    normalized_department = normalize_department(department)
    if normalized_department not in {"client", "purchasing"}:
        return 0

    if category == "new":
        status_sql = "status = 'new'"
    elif category == "work":
        status_sql = "status IN ('in_work', 'waiting_answer', 'waiting_confirmation')"
    else:
        return 0

    db = await get_db()
    cursor = await db.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM tickets
        WHERE is_deleted = 0
          AND executor_department = ?
          AND {status_sql}
          AND (snoozed_until IS NULL OR snoozed_until <= CURRENT_TIMESTAMP)
        """,
        (normalized_department,),
    )
    row = await cursor.fetchone()
    await db.close()
    return int(row["total"] or 0) if row else 0



async def take_ticket(
    ticket_id: int,
    telegram_id: int,
    *,
    comment: str | None = None,
) -> bool:
    """Берёт только новый тикет; повторное одновременное нажатие безопасно."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET status = 'in_work',
                taken_by = ?,
                assigned_at = CURRENT_TIMESTAMP,
                assigned_by = ?,
                auto_close_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND is_deleted = 0
              AND status = 'new'
              AND taken_by IS NULL
            """,
            (telegram_id, telegram_id, ticket_id),
        )
        changed = cursor.rowcount > 0
        if not changed:
            await db.rollback()
            return False
        if comment:
            await db.execute(
                """
                INSERT INTO ticket_comments (ticket_id, user_id, comment, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (ticket_id, telegram_id, comment),
            )
            await _insert_ticket_event(db, ticket_id, "comment", telegram_id, "Добавлен комментарий")
        await _insert_ticket_event(db, ticket_id, "taken", telegram_id, "Тикет взят в работу")
        await db.execute(
            "INSERT INTO ticket_assignment_history(ticket_id,from_user_id,to_user_id,actor_id,reason) VALUES(?,NULL,?,?,'taken')",
            (ticket_id, telegram_id, telegram_id),
        )
        await db.execute(
            """
            INSERT INTO ticket_metrics(ticket_id,first_taken_at,assignment_count,updated_at)
            VALUES(?,CURRENT_TIMESTAMP,1,CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id) DO UPDATE SET
                first_taken_at=COALESCE(ticket_metrics.first_taken_at,CURRENT_TIMESTAMP),
                assignment_count=ticket_metrics.assignment_count+1,
                updated_at=CURRENT_TIMESTAMP
            """,
            (ticket_id,),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

async def get_overdue_client_tickets():
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username,
            CAST(
                JULIANDAY('now') - JULIANDAY(COALESCE(t.updated_at, t.created_at))
                AS INTEGER
            ) AS overdue_days
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        WHERE t.is_deleted = 0
          AND t.requester_department = 'purchasing'
          AND t.executor_department = 'client'
          AND t.status NOT IN ('done', 'cancelled')
          AND (t.snoozed_until IS NULL OR t.snoozed_until <= CURRENT_TIMESTAMP)
          AND JULIANDAY('now') - JULIANDAY(COALESCE(t.updated_at, t.created_at)) >= 2
        ORDER BY overdue_days DESC, COALESCE(t.updated_at, t.created_at) ASC
        """
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def get_observer_active_tickets():
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        WHERE t.is_deleted = 0
          AND t.status NOT IN ('done', 'cancelled')
        ORDER BY t.created_at DESC
        """
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def get_observer_closed_tickets():
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        WHERE t.is_deleted = 0
          AND t.status IN ('done', 'cancelled')
        ORDER BY COALESCE(t.closed_at, t.updated_at, t.created_at) DESC
        """
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def get_observer_report(period: str | None = None, telegram_id: int | None = None):
    db = await get_db()

    params = []

    where_sql = """
        WHERE t.is_deleted = 0
          AND t.excluded_from_stats = 0
    """

    if period == "day":
        where_sql += """
          AND DATETIME(t.created_at) >= DATETIME('now', '-1 day')
        """
    elif period == "week":
        where_sql += """
          AND DATETIME(t.created_at) >= DATETIME('now', '-7 day')
        """
    elif period == "month":
        where_sql += """
          AND DATETIME(t.created_at) >= DATETIME('now', '-30 day')
        """

    if telegram_id is not None:
        where_sql += """
          AND t.created_by = ?
        """
        params.append(telegram_id)

    cursor = await db.execute(
        f"""
        SELECT
            COUNT(*) AS total_created,
            SUM(CASE WHEN t.status NOT IN ('done', 'cancelled') THEN 1 ELSE 0 END) AS open_total,
            SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS done_total,
            SUM(CASE WHEN t.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_total,
            AVG(
                CASE
                    WHEN t.status IN ('done', 'cancelled') AND t.closed_at IS NOT NULL
                    THEN (JULIANDAY(t.closed_at) - JULIANDAY(t.created_at)) * 24 * 60
                    ELSE NULL
                END
            ) AS avg_minutes
        FROM tickets t
        {where_sql}
        """,
        tuple(params)
    )

    report = await cursor.fetchone()
    await db.close()

    return report


async def get_users_for_observer_report():
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT DISTINCT
            u.telegram_id,
            u.username,
            u.full_name,
            u.role
        FROM users u
        INNER JOIN tickets t ON t.created_by = u.telegram_id
        WHERE u.is_active = 1
          AND t.is_deleted = 0
        ORDER BY u.full_name ASC, u.username ASC, u.telegram_id ASC
        """
    )

    users = await cursor.fetchall()
    await db.close()

    return users


async def get_admin_tickets_page(ticket_filter: str = "all", limit: int = 10, offset: int = 0):
    db = await get_db()

    if ticket_filter == "open":
        where_sql = """
        WHERE t.is_deleted = 0
          AND t.status IN ('new', 'in_work', 'waiting_answer', 'waiting_confirmation')
        """
        order_sql = "ORDER BY t.created_at DESC"
    elif ticket_filter == "closed":
        where_sql = """
        WHERE t.is_deleted = 0
          AND t.status IN ('done', 'cancelled')
        """
        order_sql = "ORDER BY COALESCE(t.closed_at, t.updated_at, t.created_at) DESC"
    elif ticket_filter == "deleted":
        where_sql = """
        WHERE t.is_deleted = 1
        """
        order_sql = "ORDER BY COALESCE(t.deleted_at, t.updated_at, t.created_at) DESC"
    else:
        where_sql = """
        WHERE t.is_deleted = 0
        """
        order_sql = "ORDER BY t.created_at DESC"

    cursor = await db.execute(
        f"""
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        {where_sql}
        {order_sql}
        LIMIT ?
        OFFSET ?
        """,
        (limit, offset)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def count_admin_tickets(ticket_filter: str = "all") -> int:
    db = await get_db()

    if ticket_filter == "open":
        where_sql = """
        WHERE is_deleted = 0
          AND status IN ('new', 'in_work', 'waiting_answer', 'waiting_confirmation')
        """
    elif ticket_filter == "closed":
        where_sql = """
        WHERE is_deleted = 0
          AND status IN ('done', 'cancelled')
        """
    elif ticket_filter == "deleted":
        where_sql = """
        WHERE is_deleted = 1
        """
    else:
        where_sql = """
        WHERE is_deleted = 0
        """

    cursor = await db.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM tickets
        {where_sql}
        """
    )

    row = await cursor.fetchone()
    await db.close()

    if not row:
        return 0

    return row["total"] or 0


async def get_tickets_by_user_admin(telegram_id: int, limit: int = 50):
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        WHERE t.created_by = ?
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (telegram_id, limit)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def soft_delete_ticket(ticket_id: int, admin_telegram_id: int):
    db = await get_db()

    await db.execute(
        """
        UPDATE tickets
        SET
            is_deleted = 1,
            deleted_at = CURRENT_TIMESTAMP,
            deleted_by = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (admin_telegram_id, ticket_id)
    )

    await db.commit()
    await db.close()


async def restore_ticket(ticket_id: int, admin_telegram_id: int):
    db = await get_db()

    await db.execute(
        """
        UPDATE tickets
        SET
            is_deleted = 0,
            restored_at = CURRENT_TIMESTAMP,
            restored_by = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (admin_telegram_id, ticket_id)
    )

    await db.commit()
    await db.close()


async def set_ticket_status_admin(ticket_id: int, status: str, admin_telegram_id: int):
    db = await get_db()

    if status in CLOSED_STATUSES:
        await db.execute(
            """
            UPDATE tickets
            SET
                status = ?,
                closed_at = COALESCE(closed_at, CURRENT_TIMESTAMP),
                auto_close_at = NULL,
                updated_at = CURRENT_TIMESTAMP,
                admin_note = COALESCE(admin_note, '') || ?
            WHERE id = ?
            """,
            (
                status,
                f"\nСтатус изменён администратором {admin_telegram_id}.",
                ticket_id
            )
        )
    elif status == "in_work":
        await db.execute(
            """
            UPDATE tickets
            SET
                status = ?,
                closed_at = NULL,
                auto_close_at = NULL,
                reopened_at = CASE
                    WHEN closed_at IS NOT NULL THEN CURRENT_TIMESTAMP
                    ELSE reopened_at
                END,
                updated_at = CURRENT_TIMESTAMP,
                admin_note = COALESCE(admin_note, '') || ?
            WHERE id = ?
            """,
            (
                status,
                f"\nСтатус изменён администратором {admin_telegram_id}.",
                ticket_id
            )
        )
    else:
        await db.execute(
            """
            UPDATE tickets
            SET
                status = ?,
                auto_close_at = NULL,
                updated_at = CURRENT_TIMESTAMP,
                admin_note = COALESCE(admin_note, '') || ?
            WHERE id = ?
            """,
            (
                status,
                f"\nСтатус изменён администратором {admin_telegram_id}.",
                ticket_id
            )
        )

    await db.commit()
    await db.close()


async def get_admin_ticket_stats():
    db = await get_db()

    cursor = await db.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN is_deleted = 0 THEN 1 ELSE 0 END) AS visible_total,
            SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) AS deleted_total,
            SUM(CASE WHEN is_deleted = 0 AND status IN ('new', 'in_work', 'waiting_answer', 'waiting_confirmation') THEN 1 ELSE 0 END) AS open_total,
            SUM(CASE WHEN is_deleted = 0 AND status IN ('done', 'cancelled') THEN 1 ELSE 0 END) AS closed_total,
            SUM(CASE WHEN executor_department = 'purchasing' THEN 1 ELSE 0 END) AS purchasing_total,
            SUM(CASE WHEN executor_department = 'client' THEN 1 ELSE 0 END) AS client_total
        FROM tickets
        """
    )

    stats = await cursor.fetchone()
    await db.close()

    return stats


async def get_setting(key: str, default=None):
    db = await get_db()

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    cursor = await db.execute(
        """
        SELECT value
        FROM settings
        WHERE key = ?
        """,
        (key,)
    )

    row = await cursor.fetchone()
    await db.close()

    if not row:
        return default

    return row["value"]


async def set_setting(key: str, value: str):
    db = await get_db()

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    await db.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value)
    )

    await db.commit()
    await db.close()


async def get_active_users_by_department(department: str):
    db = await get_db()

    normalized_department = normalize_department(department)

    if normalized_department == DEPARTMENT_CLIENT:
        roles = tuple(CLIENT_ROLE_ALIASES)
    elif normalized_department == DEPARTMENT_PURCHASING:
        roles = tuple(PURCHASING_ROLE_ALIASES)
    else:
        roles = (department,)

    placeholders = ", ".join(["?"] * len(roles))

    cursor = await db.execute(
        f"""
        SELECT
            telegram_id,
            username,
            full_name,
            role,
            is_active
        FROM users
        WHERE is_active = 1
          AND role IN ({placeholders})
          AND NOT (
              day_off_start IS NOT NULL AND day_off_end IS NOT NULL
              AND DATE('now', '+3 hours') BETWEEN day_off_start AND day_off_end
          )
        ORDER BY full_name ASC, username ASC, telegram_id ASC
        """,
        roles
    )

    users = await cursor.fetchall()
    await db.close()

    return users


async def search_archive_tickets(
    query: str,
    telegram_id: int | None = None,
    department: str | None = None,
    is_observer: bool = False,
    is_admin: bool = False,
    limit: int | None = 200,
):
    db = await get_db()

    query = str(query or "").strip()

    if not query:
        await db.close()
        return []

    normalized_department = normalize_department(department)
    like_query = f"%{query}%"

    params = []

    where_sql = """
        WHERE t.is_deleted = 0
          AND t.status IN ('done', 'cancelled')
          AND (
                COALESCE(t.order_number, '') LIKE ?
                OR COALESCE(t.description, '') LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM ticket_comments c
                    WHERE c.ticket_id = t.id
                      AND COALESCE(c.comment, '') LIKE ?
                )
          )
    """

    params.extend(
        [
            like_query,
            like_query,
            like_query,
        ]
    )

    if not is_admin and not is_observer:
        where_sql += """
          AND (
                t.created_by = ?
                OR t.executor_department = ?
          )
        """
        params.extend(
            [
                telegram_id,
                normalized_department,
            ]
        )

    limit_sql = ""

    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)

    cursor = await db.execute(
        f"""
        SELECT
            t.*,
            u.full_name AS creator_full_name,
            u.username AS creator_username
        FROM tickets t
        LEFT JOIN users u ON u.telegram_id = t.created_by
        {where_sql}
        ORDER BY COALESCE(t.closed_at, t.updated_at, t.created_at) DESC
        {limit_sql}
        """,
        tuple(params)
    )

    tickets = await cursor.fetchall()
    await db.close()

    return tickets


async def set_ticket_priority(ticket_id: int, priority: str, actor_telegram_id: int | None = None):
    if priority not in ALLOWED_PRIORITIES:
        raise ValueError("Недопустимый приоритет")
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE tickets SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND is_deleted = 0",
            (priority, ticket_id),
        )
        changed = cursor.rowcount > 0
        if changed:
            await _insert_ticket_event(db, ticket_id, "priority_changed", actor_telegram_id, f"Приоритет: {priority}")
            await db.commit()
        else:
            await db.rollback()
        return changed
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def set_ticket_category(ticket_id: int, category: str | None, actor_telegram_id: int | None = None):
    if category not in ALLOWED_CATEGORIES:
        raise ValueError("Недопустимый тип тикета")
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE tickets SET category = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND is_deleted = 0",
            (category, ticket_id),
        )
        changed = cursor.rowcount > 0
        if changed:
            await _insert_ticket_event(db, ticket_id, "category_changed", actor_telegram_id, f"Тип: {category or 'не выбран'}")
            await db.commit()
        else:
            await db.rollback()
        return changed
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

async def get_filtered_tickets(
    telegram_id: int,
    department: str | None,
    list_type: str,
    status: str | None = None,
    priority: str | None = None,
    category: str | None = None,
    has_attachments: bool | None = None,
    date_days: int | None = None,
    overdue_only: bool = False,
    filter_department: str | None = None,
    limit: int = 50,
):
    db = await get_db()
    params = []
    where = ["t.is_deleted = 0"]
    if list_type == "outgoing":
        where.append("t.created_by = ?")
        params.append(telegram_id)
    elif list_type == "incoming":
        where.append("(t.snoozed_until IS NULL OR t.snoozed_until <= CURRENT_TIMESTAMP)")
        if department:
            where.append("t.executor_department = ?")
            params.append(normalize_department(department))
        where.append("t.status NOT IN ('done','cancelled')")
    elif list_type == "work":
        where.append("t.status = 'in_work'")
        where.append("(t.snoozed_until IS NULL OR t.snoozed_until <= CURRENT_TIMESTAMP)")
        if department:
            where.append("t.executor_department = ?")
            params.append(normalize_department(department))
    elif list_type == "archive":
        where.append("t.status IN ('done','cancelled')")
        if department:
            where.append("(t.created_by = ? OR t.executor_department = ?)")
            params.extend([telegram_id, normalize_department(department)])
    if status:
        where.append("t.status = ?")
        params.append(status)
    if priority:
        where.append("COALESCE(t.priority, 'normal') = ?")
        params.append(priority)
    if category:
        where.append("t.category = ?")
        params.append(category)
    if has_attachments is not None:
        where.append(("EXISTS" if has_attachments else "NOT EXISTS") + " (SELECT 1 FROM ticket_attachments a WHERE a.ticket_id = t.id)")
    if date_days:
        where.append("JULIANDAY('now') - JULIANDAY(t.created_at) <= ?")
        params.append(int(date_days))
    if overdue_only:
        where.append("t.status NOT IN ('done','cancelled')")
        where.append("JULIANDAY('now') - JULIANDAY(COALESCE(t.updated_at,t.created_at)) >= 2")
    if filter_department:
        where.append("(t.requester_department = ? OR t.executor_department = ?)")
        params.extend([normalize_department(filter_department), normalize_department(filter_department)])
    params.append(limit)
    cursor = await db.execute(
        f"""SELECT t.*, u.full_name AS creator_full_name, u.username AS creator_username
        FROM tickets t LEFT JOIN users u ON u.telegram_id=t.created_by
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(t.updated_at,t.created_at) DESC LIMIT ?""", tuple(params))
    rows = await cursor.fetchall()
    await db.close()
    return rows
