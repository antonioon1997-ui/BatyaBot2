from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.database import get_db
from app.domain import CLOSED_STATUSES, OPEN_STATUSES, department_by_role, normalize_department

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _utc_sql(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MOSCOW_TZ)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _moscow_date(offset_days: int = 0) -> date:
    return datetime.now(MOSCOW_TZ).date() + timedelta(days=offset_days)


def _is_open_status(status: str | None) -> bool:
    return status in OPEN_STATUSES


async def get_assignment_candidates(department: str, *, exclude_user_id: int | None = None):
    department = normalize_department(department)
    if department not in {"client", "purchasing"}:
        return []
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT *
            FROM users
            WHERE is_active = 1
              AND (
                    (? = 'purchasing' AND LOWER(COALESCE(role, '')) IN ('purchasing','purchaser','purchase','buyer','zakup','zakupki','закупка','закупки','закупщик'))
                 OR (? = 'client' AND LOWER(COALESCE(role, '')) IN ('client','customer','sales','manager','client_department','клиент','клиентский','клиентский отдел'))
              )
              AND (? IS NULL OR telegram_id != ?)
              AND NOT (
                    day_off_start IS NOT NULL AND day_off_end IS NOT NULL
                    AND DATE('now', '+3 hours') BETWEEN day_off_start AND day_off_end
              )
            ORDER BY COALESCE(full_name, username, CAST(telegram_id AS TEXT)) COLLATE NOCASE
            """,
            (department, department, exclude_user_id, exclude_user_id),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def assign_ticket(
    ticket_id: int,
    to_user_id: int | None,
    actor_id: int,
    *,
    expected_assignee: int | None | object = Ellipsis,
    reason: str = "assigned",
) -> bool:
    """Назначает или освобождает тикет атомарно.

    expected_assignee=Ellipsis не проверяет прежнего исполнителя; None требует общий тикет.
    """
    db = await get_db()
    try:
        # Сериализуем назначение: два одновременных нажатия не смогут оба победить.
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT status, taken_by, executor_department FROM tickets WHERE id = ? AND is_deleted = 0",
            (ticket_id,),
        )
        ticket = await cursor.fetchone()
        if not ticket or not _is_open_status(ticket["status"]):
            await db.rollback()
            return False

        old_assignee = ticket["taken_by"]
        if expected_assignee is not Ellipsis and old_assignee != expected_assignee:
            await db.rollback()
            return False

        if to_user_id is not None:
            cursor = await db.execute(
                "SELECT role, is_active, day_off_start, day_off_end FROM users WHERE telegram_id = ?",
                (to_user_id,),
            )
            target = await cursor.fetchone()
            if not target or int(target["is_active"] or 0) != 1:
                await db.rollback()
                return False
            if department_by_role(target["role"]) != normalize_department(ticket["executor_department"]):
                await db.rollback()
                return False
            today = _moscow_date().isoformat()
            if target["day_off_start"] and target["day_off_end"] and target["day_off_start"] <= today <= target["day_off_end"]:
                await db.rollback()
                return False

        cursor = await db.execute(
            """
            UPDATE tickets
            SET taken_by = ?, assigned_at = CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
                assigned_by = ?,
                status = CASE WHEN status = 'new' AND ? IS NOT NULL THEN 'in_work' ELSE status END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND is_deleted = 0
            """,
            (to_user_id, to_user_id, actor_id, to_user_id, ticket_id),
        )
        if cursor.rowcount <= 0:
            await db.rollback()
            return False

        await db.execute(
            """
            INSERT INTO ticket_assignment_history(ticket_id, from_user_id, to_user_id, actor_id, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticket_id, old_assignee, to_user_id, actor_id, reason),
        )
        await db.execute(
            """
            INSERT INTO ticket_events(ticket_id, actor_telegram_id, event_type, details)
            VALUES (?, ?, 'assignment_changed', ?)
            """,
            (ticket_id, actor_id, f"Исполнитель: {old_assignee or 'общий'} → {to_user_id or 'общий'}; причина: {reason}"),
        )
        await db.execute(
            """
            INSERT INTO ticket_metrics(ticket_id, first_taken_at, assignment_count, updated_at)
            VALUES (?, CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
                    CASE WHEN ? IS NULL THEN 0 ELSE 1 END, CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id) DO UPDATE SET
                first_taken_at = CASE WHEN ? IS NULL THEN ticket_metrics.first_taken_at ELSE COALESCE(ticket_metrics.first_taken_at, CURRENT_TIMESTAMP) END,
                assignment_count = ticket_metrics.assignment_count + CASE WHEN ? IS NULL THEN 0 ELSE 1 END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (ticket_id, to_user_id, to_user_id, to_user_id, to_user_id),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def create_transfer_request(ticket_id: int, requester_id: int) -> int | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT status, taken_by, executor_department FROM tickets WHERE id = ? AND is_deleted = 0",
            (ticket_id,),
        )
        ticket = await cursor.fetchone()
        if not ticket or not _is_open_status(ticket["status"]) or not ticket["taken_by"]:
            return None
        if int(ticket["taken_by"]) == int(requester_id):
            return None
        cursor = await db.execute("SELECT role, is_active FROM users WHERE telegram_id = ?", (requester_id,))
        requester = await cursor.fetchone()
        if not requester or int(requester["is_active"] or 0) != 1:
            return None
        if department_by_role(requester["role"]) != normalize_department(ticket["executor_department"]):
            return None
        await db.execute(
            "UPDATE ticket_transfer_requests SET status = 'cancelled', processed_at = CURRENT_TIMESTAMP WHERE ticket_id = ? AND requester_id = ? AND status = 'pending'",
            (ticket_id, requester_id),
        )
        cursor = await db.execute(
            """
            INSERT INTO ticket_transfer_requests(ticket_id, requester_id, current_assignee_id)
            VALUES (?, ?, ?)
            """,
            (ticket_id, requester_id, int(ticket["taken_by"])),
        )
        request_id = int(cursor.lastrowid)
        await db.commit()
        return request_id
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_transfer_request(request_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT r.*, t.executor_department, t.status AS ticket_status, t.taken_by,
                   u.full_name AS requester_name, u.username AS requester_username
            FROM ticket_transfer_requests r
            JOIN tickets t ON t.id = r.ticket_id
            LEFT JOIN users u ON u.telegram_id = r.requester_id
            WHERE r.id = ?
            """,
            (request_id,),
        )
        return await cursor.fetchone()
    finally:
        await db.close()


async def process_transfer_request(request_id: int, actor_id: int, approve: bool) -> tuple[bool, int | None, int | None]:
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM ticket_transfer_requests WHERE id = ?", (request_id,))
        request = await cursor.fetchone()
        if not request or request["status"] != "pending" or int(request["current_assignee_id"]) != int(actor_id):
            await db.rollback()
            return False, None, None
        cursor = await db.execute("SELECT status, taken_by, executor_department FROM tickets WHERE id = ? AND is_deleted = 0", (request["ticket_id"],))
        ticket = await cursor.fetchone()
        if not ticket or not _is_open_status(ticket["status"]) or int(ticket["taken_by"] or 0) != int(actor_id):
            await db.execute(
                "UPDATE ticket_transfer_requests SET status='stale', processed_at=CURRENT_TIMESTAMP, processed_by=? WHERE id=?",
                (actor_id, request_id),
            )
            await db.commit()
            return False, int(request["ticket_id"]), int(request["requester_id"])
        if approve:
            cursor = await db.execute(
                "SELECT role,is_active,day_off_start,day_off_end FROM users WHERE telegram_id=?",
                (request["requester_id"],),
            )
            target = await cursor.fetchone()
            today = _moscow_date().isoformat()
            target_unavailable = (
                not target
                or int(target["is_active"] or 0) != 1
                or department_by_role(target["role"]) != normalize_department(ticket["executor_department"])
                or (
                    target["day_off_start"]
                    and target["day_off_end"]
                    and target["day_off_start"] <= today <= target["day_off_end"]
                )
            )
            if target_unavailable:
                await db.execute(
                    "UPDATE ticket_transfer_requests SET status='stale',processed_at=CURRENT_TIMESTAMP,processed_by=? WHERE id=?",
                    (actor_id, request_id),
                )
                await db.commit()
                return False, int(request["ticket_id"]), int(request["requester_id"])

        status = "approved" if approve else "rejected"
        await db.execute(
            "UPDATE ticket_transfer_requests SET status=?, processed_at=CURRENT_TIMESTAMP, processed_by=? WHERE id=?",
            (status, actor_id, request_id),
        )
        if approve:
            await db.execute(
                "UPDATE tickets SET taken_by=?, assigned_at=CURRENT_TIMESTAMP, assigned_by=?, status=CASE WHEN status='new' THEN 'in_work' ELSE status END, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (request["requester_id"], actor_id, request["ticket_id"]),
            )
            await db.execute(
                "INSERT INTO ticket_assignment_history(ticket_id, from_user_id, to_user_id, actor_id, reason) VALUES (?, ?, ?, ?, 'transfer_request_approved')",
                (request["ticket_id"], actor_id, request["requester_id"], actor_id),
            )
            await db.execute(
                "INSERT INTO ticket_events(ticket_id, actor_telegram_id, event_type, details) VALUES (?, ?, 'assignment_changed', ?)",
                (request["ticket_id"], actor_id, f"Передан по запросу сотруднику {request['requester_id']}"),
            )
            await db.execute(
                """
                INSERT INTO ticket_metrics(ticket_id, first_taken_at, assignment_count, updated_at)
                VALUES (?,CURRENT_TIMESTAMP,1,CURRENT_TIMESTAMP)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    first_taken_at=COALESCE(ticket_metrics.first_taken_at,CURRENT_TIMESTAMP),
                    assignment_count=ticket_metrics.assignment_count+1,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (request["ticket_id"],),
            )
        await db.commit()
        return True, int(request["ticket_id"]), int(request["requester_id"])
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_assigned_tickets(user_id: int, limit: int = 40):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT t.*, c.full_name AS creator_full_name, c.username AS creator_username,
                   a.full_name AS assignee_full_name, a.username AS assignee_username,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM ticket_events e
                       LEFT JOIN ticket_reads r ON r.ticket_id=t.id AND r.user_id=?
                       WHERE e.ticket_id=t.id AND e.id>COALESCE(r.last_event_id,0)
                         AND COALESCE(e.actor_telegram_id,0) != ?
                   ) THEN 1 ELSE 0 END AS has_unread
            FROM tickets t
            LEFT JOIN users c ON c.telegram_id=t.created_by
            LEFT JOIN users a ON a.telegram_id=t.taken_by
            WHERE t.is_deleted=0 AND t.status NOT IN ('done','cancelled')
              AND t.taken_by=?
              AND (t.snoozed_until IS NULL OR t.snoozed_until <= CURRENT_TIMESTAMP)
            ORDER BY COALESCE(t.updated_at,t.created_at) DESC LIMIT ?
            """,
            (user_id, user_id, user_id, limit),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_common_tickets(user_id: int, department: str, limit: int = 40):
    department = normalize_department(department)
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT t.*, c.full_name AS creator_full_name, c.username AS creator_username,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM ticket_events e
                       LEFT JOIN ticket_reads r ON r.ticket_id=t.id AND r.user_id=?
                       WHERE e.ticket_id=t.id AND e.id>COALESCE(r.last_event_id,0)
                         AND COALESCE(e.actor_telegram_id,0) != ?
                   ) THEN 1 ELSE 0 END AS has_unread
            FROM tickets t
            LEFT JOIN users c ON c.telegram_id=t.created_by
            WHERE t.is_deleted=0 AND t.status NOT IN ('done','cancelled')
              AND t.executor_department=? AND t.taken_by IS NULL
              AND (t.snoozed_until IS NULL OR t.snoozed_until <= CURRENT_TIMESTAMP)
            ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'important' THEN 1 ELSE 2 END,
                     COALESCE(t.updated_at,t.created_at) DESC LIMIT ?
            """,
            (user_id, user_id, department, limit),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def mark_ticket_read(ticket_id: int, user_id: int) -> None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COALESCE(MAX(id),0) AS last_id FROM ticket_events WHERE ticket_id=?", (ticket_id,))
        row = await cursor.fetchone()
        last_id = int(row["last_id"] or 0)
        await db.execute(
            """
            INSERT INTO ticket_reads(ticket_id,user_id,last_event_id,read_at)
            VALUES(?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id,user_id) DO UPDATE SET last_event_id=excluded.last_event_id, read_at=CURRENT_TIMESTAMP
            """,
            (ticket_id, user_id, last_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_unread_active_tickets(user_id: int, department: str | None, *, is_admin: bool = False, limit: int = 40):
    db = await get_db()
    try:
        params: list = []
        access = "1=1" if is_admin else "(t.created_by=? OR t.executor_department=? OR t.requester_department=?)"
        if not is_admin:
            normalized_department = normalize_department(department)
            params.extend([user_id, normalized_department, normalized_department])
        params.extend([user_id, user_id, limit])
        cursor = await db.execute(
            f"""
            SELECT t.*, c.full_name AS creator_full_name, c.username AS creator_username,
                   a.full_name AS assignee_full_name, a.username AS assignee_username, 1 AS has_unread
            FROM tickets t
            LEFT JOIN users c ON c.telegram_id=t.created_by
            LEFT JOIN users a ON a.telegram_id=t.taken_by
            WHERE t.is_deleted=0 AND t.status NOT IN ('done','cancelled')
              AND {access}
              AND EXISTS (
                  SELECT 1 FROM ticket_events e
                  LEFT JOIN ticket_reads r ON r.ticket_id=t.id AND r.user_id=?
                  WHERE e.ticket_id=t.id AND e.id>COALESCE(r.last_event_id,0)
                    AND COALESCE(e.actor_telegram_id,0) != ?
              )
            ORDER BY COALESCE(t.updated_at,t.created_at) DESC LIMIT ?
            """,
            tuple(params),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def count_unread_active_tickets(user_id: int, department: str | None, *, is_admin: bool = False) -> int:
    db = await get_db()
    try:
        params: list = []
        access = "1=1" if is_admin else "(t.created_by=? OR t.executor_department=? OR t.requester_department=?)"
        if not is_admin:
            normalized_department = normalize_department(department)
            params.extend([user_id, normalized_department, normalized_department])
        params.extend([user_id, user_id])
        cursor = await db.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM tickets t
            WHERE t.is_deleted=0 AND t.status NOT IN ('done','cancelled')
              AND {access}
              AND EXISTS (
                  SELECT 1 FROM ticket_events e
                  LEFT JOIN ticket_reads r ON r.ticket_id=t.id AND r.user_id=?
                  WHERE e.ticket_id=t.id AND e.id>COALESCE(r.last_event_id,0)
                    AND COALESCE(e.actor_telegram_id,0) != ?
              )
            """,
            tuple(params),
        )
        row = await cursor.fetchone()
        return int(row["total"] or 0) if row else 0
    finally:
        await db.close()


async def search_active_tickets(query: str, user_id: int, department: str | None, *, is_admin: bool = False, limit: int = 30):
    value = query.strip()
    if not value:
        return []
    db = await get_db()
    try:
        params: list = []
        access = "1=1" if is_admin else "(t.created_by=? OR t.executor_department=? OR t.requester_department=?)"
        if not is_admin:
            normalized_department = normalize_department(department)
            params.extend([user_id, normalized_department, normalized_department])
        like = f"%{value}%"
        exact_id = int(value.lstrip("#")) if value.lstrip("#").isdigit() else -1
        params.extend([exact_id, like, like, like, like, like, like, limit])
        cursor = await db.execute(
            f"""
            SELECT DISTINCT t.*, c.full_name AS creator_full_name, c.username AS creator_username,
                   a.full_name AS assignee_full_name, a.username AS assignee_username
            FROM tickets t
            LEFT JOIN users c ON c.telegram_id=t.created_by
            LEFT JOIN users a ON a.telegram_id=t.taken_by
            LEFT JOIN ticket_comments cm ON cm.ticket_id=t.id
            WHERE t.is_deleted=0 AND t.status NOT IN ('done','cancelled') AND {access}
              AND (t.id=? OR COALESCE(t.order_number,'') LIKE ? OR COALESCE(t.title,'') LIKE ?
                   OR COALESCE(t.description,'') LIKE ? OR COALESCE(cm.comment,'') LIKE ?
                   OR COALESCE(c.full_name,'') LIKE ? OR COALESCE(c.username,'') LIKE ?)
            ORDER BY COALESCE(t.updated_at,t.created_at) DESC LIMIT ?
            """,
            tuple(params),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def find_open_duplicates(order_number: str, *, exclude_ticket_id: int | None = None, limit: int = 10):
    if not order_number or not order_number.strip():
        return []
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT t.*, u.full_name AS creator_full_name, u.username AS creator_username
            FROM tickets t LEFT JOIN users u ON u.telegram_id=t.created_by
            WHERE t.is_deleted=0 AND t.status NOT IN ('done','cancelled')
              AND LOWER(TRIM(COALESCE(t.order_number,'')))=LOWER(TRIM(?))
              AND (? IS NULL OR t.id != ?)
            ORDER BY t.created_at DESC LIMIT ?
            """,
            (order_number, exclude_ticket_id, exclude_ticket_id, limit),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def set_day_off(user_id: int, start_offset: int, duration_days: int, actor_id: int) -> tuple[str, str, list[int]]:
    if start_offset not in {0, 1} or duration_days not in {1, 2, 3}:
        raise ValueError("Неверный период выходных")
    start = _moscow_date(start_offset)
    end = start + timedelta(days=duration_days - 1)
    start_s, end_s = start.isoformat(), end.isoformat()
    db = await get_db()
    released: list[int] = []
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "UPDATE users SET day_off_start=?, day_off_end=?, day_off_set_by=?, day_off_updated_at=CURRENT_TIMESTAMP WHERE telegram_id=? AND is_active=1",
            (start_s, end_s, actor_id, user_id),
        )
        if start_offset == 0:
            cursor = await db.execute(
                "SELECT id FROM tickets WHERE taken_by=? AND is_deleted=0 AND status NOT IN ('done','cancelled')",
                (user_id,),
            )
            released = [int(row["id"]) for row in await cursor.fetchall()]
            for ticket_id in released:
                await db.execute(
                    "UPDATE tickets SET taken_by=NULL, assigned_at=NULL, assigned_by=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND taken_by=?",
                    (actor_id, ticket_id, user_id),
                )
                await db.execute(
                    """
                    INSERT INTO day_off_releases(user_id,ticket_id,day_off_start,day_off_end,restored,created_at)
                    VALUES(?,?,?,?,0,CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id,ticket_id,day_off_start,day_off_end)
                    DO UPDATE SET restored=0, created_at=CURRENT_TIMESTAMP
                    """,
                    (user_id, ticket_id, start_s, end_s),
                )
                await db.execute(
                    "INSERT INTO ticket_assignment_history(ticket_id,from_user_id,to_user_id,actor_id,reason) VALUES(?,?,NULL,?,'day_off_release')",
                    (ticket_id, user_id, actor_id),
                )
                await db.execute(
                    "INSERT INTO ticket_events(ticket_id,actor_telegram_id,event_type,details) VALUES(?,?,'assignment_changed','Исполнитель ушёл на выходной; тикет возвращён в общий список')",
                    (ticket_id, actor_id),
                )
        await db.commit()
        return start_s, end_s, released
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def activate_scheduled_day_offs() -> list[tuple[int, list[int]]]:
    today = _moscow_date().isoformat()
    db = await get_db()
    results: list[tuple[int, list[int]]] = []
    try:
        cursor = await db.execute(
            "SELECT telegram_id, day_off_start, day_off_end FROM users WHERE is_active=1 AND day_off_start=?",
            (today,),
        )
        users = await cursor.fetchall()
        for user in users:
            user_id = int(user["telegram_id"])
            cursor = await db.execute(
                "SELECT id FROM tickets WHERE taken_by=? AND is_deleted=0 AND status NOT IN ('done','cancelled')",
                (user_id,),
            )
            ticket_ids = [int(row["id"]) for row in await cursor.fetchall()]
            released = []
            for ticket_id in ticket_ids:
                cursor2 = await db.execute(
                    "UPDATE tickets SET taken_by=NULL,assigned_at=NULL,assigned_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND taken_by=?",
                    (user_id, ticket_id, user_id),
                )
                if cursor2.rowcount <= 0:
                    continue
                released.append(ticket_id)
                await db.execute(
                    """
                    INSERT INTO day_off_releases(user_id,ticket_id,day_off_start,day_off_end,restored,created_at)
                    VALUES(?,?,?,?,0,CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id,ticket_id,day_off_start,day_off_end)
                    DO UPDATE SET restored=0, created_at=CURRENT_TIMESTAMP
                    """,
                    (user_id, ticket_id, user["day_off_start"], user["day_off_end"]),
                )
                await db.execute(
                    "INSERT INTO ticket_assignment_history(ticket_id,from_user_id,to_user_id,actor_id,reason) VALUES(?,?,NULL,?,'day_off_release')",
                    (ticket_id, user_id, user_id),
                )
                await db.execute(
                    "INSERT INTO ticket_events(ticket_id,actor_telegram_id,event_type,details) VALUES(?,?,'assignment_changed','Исполнитель ушёл на выходной; тикет возвращён в общий список')",
                    (ticket_id, user_id),
                )
            if released:
                results.append((user_id, released))
        await db.commit()
        return results
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def expire_finished_day_offs() -> list[tuple[int, list[int]]]:
    """Снимает завершившиеся выходные и возвращает кандидатов для добровольного восстановления."""
    today = _moscow_date().isoformat()
    db = await get_db()
    results: list[tuple[int, list[int]]] = []
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT telegram_id
            FROM users
            WHERE day_off_start IS NOT NULL AND day_off_end IS NOT NULL AND day_off_end < ?
            """,
            (today,),
        )
        for user in await cursor.fetchall():
            user_id = int(user["telegram_id"])
            cursor2 = await db.execute(
                """
                SELECT DISTINCT r.ticket_id FROM day_off_releases r
                JOIN tickets t ON t.id=r.ticket_id
                WHERE r.user_id=? AND r.restored=0
                  AND t.is_deleted=0 AND t.status NOT IN ('done','cancelled') AND t.taken_by IS NULL
                ORDER BY r.ticket_id
                """,
                (user_id,),
            )
            candidates = [int(row["ticket_id"]) for row in await cursor2.fetchall()]
            await db.execute(
                """
                UPDATE users SET day_off_start=NULL,day_off_end=NULL,
                    day_off_updated_at=CURRENT_TIMESTAMP
                WHERE telegram_id=?
                """,
                (user_id,),
            )
            results.append((user_id, candidates))
        await db.commit()
        return results
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def clear_day_off(user_id: int, actor_id: int) -> list[int]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (user_id,))
        if not await cursor.fetchone():
            return []
        await db.execute(
            "UPDATE users SET day_off_start=NULL,day_off_end=NULL,day_off_set_by=?,day_off_updated_at=CURRENT_TIMESTAMP WHERE telegram_id=?",
            (actor_id, user_id),
        )
        cursor = await db.execute(
            """
            SELECT DISTINCT r.ticket_id FROM day_off_releases r
            JOIN tickets t ON t.id=r.ticket_id
            WHERE r.user_id=? AND r.restored=0
              AND t.is_deleted=0 AND t.status NOT IN ('done','cancelled') AND t.taken_by IS NULL
            ORDER BY r.ticket_id
            """,
            (user_id,),
        )
        candidates = [int(row["ticket_id"]) for row in await cursor.fetchall()]
        await db.commit()
        return candidates
    finally:
        await db.close()


async def restore_day_off_tickets(user_id: int, actor_id: int) -> list[int]:
    db = await get_db()
    restored: list[int] = []
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT DISTINCT r.ticket_id FROM day_off_releases r
            JOIN tickets t ON t.id=r.ticket_id
            WHERE r.user_id=? AND r.restored=0 AND t.is_deleted=0
              AND t.status NOT IN ('done','cancelled') AND t.taken_by IS NULL
            ORDER BY r.ticket_id
            """,
            (user_id,),
        )
        candidates = [int(row["ticket_id"]) for row in await cursor.fetchall()]
        # Предложение одноразовое: тикеты, которые успел взять другой сотрудник,
        # не должны неожиданно появляться в следующем периоде выходных.
        await db.execute("UPDATE day_off_releases SET restored=1 WHERE user_id=? AND restored=0", (user_id,))
        for ticket_id in candidates:
            cursor2 = await db.execute(
                "UPDATE tickets SET taken_by=?,assigned_at=CURRENT_TIMESTAMP,assigned_by=?,status=CASE WHEN status='new' THEN 'in_work' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE id=? AND taken_by IS NULL AND status NOT IN ('done','cancelled')",
                (user_id, actor_id, ticket_id),
            )
            if cursor2.rowcount <= 0:
                continue
            restored.append(ticket_id)
            await db.execute(
                "INSERT INTO ticket_assignment_history(ticket_id,from_user_id,to_user_id,actor_id,reason) VALUES(?,NULL,?,?,'day_off_restore')",
                (ticket_id, user_id, actor_id),
            )
            await db.execute(
                "INSERT INTO ticket_events(ticket_id,actor_telegram_id,event_type,details) VALUES(?,?,'assignment_changed','Тикет возвращён прежнему исполнителю после отмены выходного')",
                (ticket_id, actor_id),
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
        return restored
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def dismiss_day_off_restore(user_id: int) -> None:
    """Оставляет освобождённые тикеты общими и закрывает текущее предложение восстановления."""
    db = await get_db()
    try:
        await db.execute("UPDATE day_off_releases SET restored=1 WHERE user_id=? AND restored=0", (user_id,))
        await db.commit()
    finally:
        await db.close()


async def set_ticket_summary(ticket_id: int, actor_id: int, *, current_summary: str | None = None, next_action: str | None = None, clear: bool = False) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM tickets WHERE id=? AND is_deleted=0", (ticket_id,))
        if not await cursor.fetchone():
            return False
        if clear:
            await db.execute(
                "UPDATE tickets SET current_summary=NULL,next_action=NULL,summary_updated_at=CURRENT_TIMESTAMP,summary_updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (actor_id, ticket_id),
            )
            detail = "Краткий итог и следующее действие очищены"
        elif current_summary is not None:
            await db.execute(
                "UPDATE tickets SET current_summary=?,summary_updated_at=CURRENT_TIMESTAMP,summary_updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (current_summary, actor_id, ticket_id),
            )
            detail = "Обновлён краткий итог"
        elif next_action is not None:
            await db.execute(
                "UPDATE tickets SET next_action=?,summary_updated_at=CURRENT_TIMESTAMP,summary_updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (next_action, actor_id, ticket_id),
            )
            detail = "Обновлено следующее действие"
        else:
            return False
        await db.execute(
            "INSERT INTO ticket_events(ticket_id,actor_telegram_id,event_type,details) VALUES(?,?,'summary_updated',?)",
            (ticket_id, actor_id, detail),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def snooze_ticket(ticket_id: int, actor_id: int, until_moscow: datetime) -> bool:
    if until_moscow <= datetime.now(MOSCOW_TZ):
        return False
    until_sql = _utc_sql(until_moscow)
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            UPDATE tickets SET snoozed_until=?,snoozed_by=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND is_deleted=0 AND status NOT IN ('done','cancelled') AND executor_department='purchasing'
            """,
            (until_sql, actor_id, ticket_id),
        )
        if cursor.rowcount <= 0:
            return False
        await db.execute(
            "INSERT INTO ticket_events(ticket_id,actor_telegram_id,event_type,details) VALUES(?,?,'snoozed',?)",
            (ticket_id, actor_id, f"Отложен до {until_moscow.strftime('%d.%m.%Y %H:%M МСК')}"),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def clear_ticket_snooze(ticket_id: int, actor_id: int, *, event_detail: str = "Отложенное состояние отменено") -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE tickets SET snoozed_until=NULL,snoozed_by=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND snoozed_until IS NOT NULL",
            (ticket_id,),
        )
        if cursor.rowcount <= 0:
            return False
        await db.execute(
            "INSERT INTO ticket_events(ticket_id,actor_telegram_id,event_type,details) VALUES(?,?,'snooze_cleared',?)",
            (ticket_id, actor_id, event_detail),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def get_due_snoozed_tickets(limit: int = 100):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT * FROM tickets WHERE is_deleted=0 AND status NOT IN ('done','cancelled')
              AND snoozed_until IS NOT NULL AND snoozed_until <= CURRENT_TIMESTAMP
            ORDER BY snoozed_until LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def wake_snoozed_ticket(ticket_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE tickets SET snoozed_until=NULL,snoozed_by=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND snoozed_until IS NOT NULL AND snoozed_until <= CURRENT_TIMESTAMP",
            (ticket_id,),
        )
        if cursor.rowcount <= 0:
            return False
        await db.execute(
            "INSERT INTO ticket_events(ticket_id,actor_telegram_id,event_type,details) VALUES(?,NULL,'snooze_due','Срок отложения завершён; тикет возвращён в рабочие списки')",
            (ticket_id,),
        )
        await db.commit()
        return True
    finally:
        await db.close()


def parse_moscow_datetime(text: str) -> datetime | None:
    value = text.strip()
    now = datetime.now(MOSCOW_TZ)
    formats = ("%d.%m.%Y %H:%M", "%d.%m %H:%M", "%Y-%m-%d %H:%M")
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%d.%m %H:%M":
                parsed = parsed.replace(year=now.year)
                if parsed.replace(tzinfo=MOSCOW_TZ) <= now - timedelta(hours=1):
                    parsed = parsed.replace(year=now.year + 1)
            return parsed.replace(tzinfo=MOSCOW_TZ)
        except ValueError:
            continue
    return None


def quick_snooze_datetime(option: str) -> datetime:
    now = datetime.now(MOSCOW_TZ)
    if option == "1h":
        return now + timedelta(hours=1)
    if option == "3h":
        return now + timedelta(hours=3)
    if option == "tomorrow10":
        return datetime.combine(now.date() + timedelta(days=1), time(10, 0), tzinfo=MOSCOW_TZ)
    raise ValueError("Неизвестный вариант")
