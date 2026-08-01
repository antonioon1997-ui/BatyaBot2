from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

from app.database import get_db
from app.domain import department_by_role, is_observer_role
from app.services.ui_versions import get_active_ui_id
from app.version import get_version


MAIN_BUTTON_CATALOG: tuple[tuple[str, str], ...] = (
    ("main.create_ticket", "➕ Создать тикет"),
    ("main.order_status", "🔎 Узнать статус заказа"),
    ("main.ticket_work", "📂 Работа с тикетами"),
    ("main.outgoing", "📤 Исходящие"),
    ("main.incoming", "📥 Входящие"),
    ("main.in_work", "🛠 В работе"),
    ("main.archive", "📦 Архив"),
    ("main.my_work", "📌 Моя работа"),
    ("main.help", "❓ Помощь"),
    ("main.admin", "⚙️ Админка"),
    ("observer.active", "🟢 Активные тикеты"),
    ("observer.closed", "✅ Закрытые тикеты"),
    ("observer.stats", "📊 Статистика"),
)

MAIN_BUTTON_LABELS = dict(MAIN_BUTTON_CATALOG)

REPLY_BUTTONS: dict[str, tuple[str, str]] = {
    label: (button_id, "main") for button_id, label in MAIN_BUTTON_CATALOG
}

CALLBACK_BUTTONS: dict[str, tuple[str, str]] = {
    "create_ticket": ("main.create_ticket", "main"),
    "order_status_start": ("main.order_status", "main"),
    "ticket_work_menu": ("main.ticket_work", "main"),
    "outgoing_tickets": ("main.outgoing", "main"),
    "incoming_tickets": ("main.incoming", "main"),
    "work_tickets": ("main.in_work", "main"),
    "archive_tickets": ("main.archive", "main"),
    "work_hub": ("main.my_work", "main"),
    "help_main": ("main.help", "main"),
    "admin_menu": ("main.admin", "main"),
    "observer_active_tickets:0": ("observer.active", "main"),
    "observer_closed_tickets:0": ("observer.closed", "main"),
    "observer_stats_menu": ("observer.stats", "main"),
}

_DYNAMIC_NUMBER_RE = re.compile(r"(?<=:)-?\d+(?=[:$])|(?<=:)-?\d+$")


def classify_reply_button(text: str | None) -> tuple[str, str, str] | None:
    value = str(text or "").strip()
    item = REPLY_BUTTONS.get(value)
    if not item:
        return None
    button_id, scope = item
    return button_id, value, scope


def normalize_callback_data(callback_data: str | None) -> str:
    value = str(callback_data or "").strip()
    if not value:
        return "callback.unknown"
    normalized = _DYNAMIC_NUMBER_RE.sub("*", value)
    normalized = normalized.replace("::", ":")
    return normalized[:180]


def callback_scope(callback_data: str) -> str:
    value = callback_data.lower()
    if value.startswith("admin_") or value.startswith(("approve_user:", "reject_user:", "set_role:", "deactivate_user:", "restore_user:")):
        return "admin"
    if value.startswith(("ticket_", "admin_ticket_", "work_", "assigned_", "common_", "unread_", "transfer_", "dayoff_", "active_search", "snooze_")):
        return "ticket"
    if value.startswith("help_"):
        return "help"
    if value.startswith(("poll_", "admin_poll")):
        return "poll"
    if value.startswith(("feedback_", "admin_feedback")):
        return "feedback"
    return "navigation"


def classify_callback_button(
    callback_data: str | None,
    button_text: str | None,
) -> tuple[str, str, str] | None:
    value = str(callback_data or "").strip()
    if not value:
        return None
    exact = CALLBACK_BUTTONS.get(value)
    label = str(button_text or "").strip() or value
    if exact:
        button_id, scope = exact
        return button_id, label, scope
    return normalize_callback_data(value), label[:180], callback_scope(value)


def _normalized_department(role: str | None) -> str:
    department = department_by_role(role)
    if department:
        return department
    if is_observer_role(role):
        return "observer"
    return "unknown"


async def record_ui_event(
    *,
    user_id: int,
    button_id: str,
    button_text: str,
    source: str,
    scope: str,
) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT role, is_active
            FROM users
            WHERE telegram_id = ?
            """,
            (int(user_id),),
        )
        user = await cursor.fetchone()
        if not user or int(user["is_active"] or 0) != 1:
            return False

        role = str(user["role"] or "unknown")
        department = _normalized_department(role)
        await db.execute(
            """
            INSERT INTO ui_button_events (
                user_id,
                role,
                department,
                button_id,
                button_text,
                source,
                scope,
                app_version,
                ui_version,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                int(user_id),
                role,
                department,
                str(button_id)[:180],
                str(button_text)[:180],
                str(source)[:32],
                str(scope)[:32],
                get_version(),
                get_active_ui_id(),
            ),
        )
        await db.commit()
        return True
    finally:
        await db.close()


def _period_modifier(days: int) -> str:
    safe_days = max(1, min(int(days), 3650))
    return f"-{safe_days} day"


async def get_button_summary(
    *,
    days: int = 30,
    department: str | None = None,
    user_id: int | None = None,
    scope: str = "main",
    order: str = "desc",
    limit: int = 50,
):
    db = await get_db()
    try:
        where = ["DATETIME(e.created_at) >= DATETIME('now', ?)"]
        params: list[object] = [_period_modifier(days)]
        if scope:
            where.append("e.scope = ?")
            params.append(scope)
        if department:
            where.append("e.department = ?")
            params.append(department)
        if user_id is not None:
            where.append("e.user_id = ?")
            params.append(int(user_id))

        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        params.append(max(1, min(int(limit), 200)))
        cursor = await db.execute(
            f"""
            SELECT
                e.button_id,
                MAX(e.button_text) AS button_text,
                COUNT(*) AS clicks,
                COUNT(DISTINCT e.user_id) AS unique_users,
                MAX(e.created_at) AS last_click_at
            FROM ui_button_events e
            WHERE {' AND '.join(where)}
            GROUP BY e.button_id
            ORDER BY clicks {direction}, e.button_id ASC
            LIMIT ?
            """,
            tuple(params),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_department_totals(*, days: int = 30, scope: str = "main"):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
                department,
                COUNT(*) AS clicks,
                COUNT(DISTINCT user_id) AS unique_users,
                COUNT(DISTINCT button_id) AS unique_buttons,
                MAX(created_at) AS last_click_at
            FROM ui_button_events
            WHERE DATETIME(created_at) >= DATETIME('now', ?)
              AND scope = ?
            GROUP BY department
            ORDER BY clicks DESC, department ASC
            """,
            (_period_modifier(days), scope),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_user_totals(*, days: int = 30, scope: str = "main"):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
                u.telegram_id,
                u.username,
                u.full_name,
                u.role,
                MAX(e.department) AS department,
                COUNT(e.id) AS clicks,
                COUNT(DISTINCT e.button_id) AS unique_buttons,
                MAX(e.created_at) AS last_click_at
            FROM users u
            LEFT JOIN ui_button_events e
              ON e.user_id = u.telegram_id
             AND DATETIME(e.created_at) >= DATETIME('now', ?)
             AND e.scope = ?
            WHERE u.is_active = 1
            GROUP BY u.telegram_id, u.username, u.full_name, u.role
            ORDER BY clicks DESC, COALESCE(u.full_name, u.username, CAST(u.telegram_id AS TEXT)) ASC
            """,
            (_period_modifier(days), scope),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_unused_main_buttons(
    *,
    days: int = 30,
    department: str | None = None,
) -> list[tuple[str, str]]:
    rows = await get_button_summary(days=days, department=department, scope="main", limit=200)
    used = {str(row["button_id"]) for row in rows}
    return [(button_id, label) for button_id, label in MAIN_BUTTON_CATALOG if button_id not in used]


async def export_ui_metrics_csv(*, days: int = 365) -> bytes:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
                e.id,
                e.created_at,
                e.user_id,
                u.username,
                u.full_name,
                e.role,
                e.department,
                e.button_id,
                e.button_text,
                e.source,
                e.scope,
                e.app_version,
                e.ui_version
            FROM ui_button_events e
            LEFT JOIN users u ON u.telegram_id = e.user_id
            WHERE DATETIME(e.created_at) >= DATETIME('now', ?)
            ORDER BY e.created_at DESC, e.id DESC
            """,
            (_period_modifier(days),),
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "id",
            "created_at_utc",
            "telegram_id",
            "username",
            "full_name",
            "role",
            "department",
            "button_id",
            "button_text",
            "source",
            "scope",
            "app_version",
            "ui_version",
        ]
    )
    for row in rows:
        writer.writerow([row[key] for key in row.keys()])
    return output.getvalue().encode("utf-8-sig")


async def delete_old_ui_events(*, keep_days: int = 365) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            DELETE FROM ui_button_events
            WHERE DATETIME(created_at) < DATETIME('now', ?)
            """,
            (_period_modifier(keep_days),),
        )
        await db.commit()
        return max(int(cursor.rowcount or 0), 0)
    finally:
        await db.close()
