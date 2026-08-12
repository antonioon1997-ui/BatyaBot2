from __future__ import annotations

import logging

from app.database import get_db
from app.domain import (
    DEPARTMENT_CLIENT,
    DEPARTMENT_PURCHASING,
    department_by_role,
    opposite_department,
)
from app.services.preferences import user_text
from app.services.ui_versions import pc_ticket_workspace_enabled
from app.services.work_management import count_unread_active_tickets

logger = logging.getLogger(__name__)

_DEPARTMENT_LABELS = {
    DEPARTMENT_CLIENT: "Клиентский отдел",
    DEPARTMENT_PURCHASING: "Закупка",
}

_WORK_STATUSES = ("in_work", "waiting_answer", "waiting_confirmation")


async def _load_dashboard_counts(department: str) -> dict[str, int]:
    opposite = opposite_department(department)
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
                SUM(CASE
                    WHEN executor_department = ? AND status = 'new'
                    THEN 1 ELSE 0 END) AS incoming_new,
                SUM(CASE
                    WHEN executor_department = ? AND status IN ('in_work', 'waiting_answer', 'waiting_confirmation')
                    THEN 1 ELSE 0 END) AS incoming_work,
                SUM(CASE
                    WHEN requester_department = ? AND executor_department = ? AND status = 'new'
                    THEN 1 ELSE 0 END) AS outgoing_new,
                SUM(CASE
                    WHEN requester_department = ? AND executor_department = ?
                         AND status IN ('in_work', 'waiting_answer', 'waiting_confirmation')
                    THEN 1 ELSE 0 END) AS outgoing_work,
                SUM(CASE
                    WHEN executor_department = ?
                         AND status IN ('new', 'in_work', 'waiting_answer', 'waiting_confirmation')
                         AND DATE(created_at, '+3 hours') < DATE('now', '+3 hours')
                    THEN 1 ELSE 0 END) AS incoming_overdue,
                SUM(CASE
                    WHEN requester_department = ? AND executor_department = ?
                         AND status IN ('new', 'in_work', 'waiting_answer', 'waiting_confirmation')
                         AND DATE(created_at, '+3 hours') < DATE('now', '+3 hours')
                    THEN 1 ELSE 0 END) AS outgoing_overdue
            FROM tickets
            WHERE is_deleted = 0
            """,
            (
                department,
                department,
                department,
                opposite,
                department,
                opposite,
                department,
                department,
                opposite,
            ),
        )
        row = await cursor.fetchone()
        if not row:
            return {
                "incoming_new": 0,
                "incoming_work": 0,
                "outgoing_new": 0,
                "outgoing_work": 0,
                "incoming_overdue": 0,
                "outgoing_overdue": 0,
            }
        return {key: int(row[key] or 0) for key in row.keys()}
    finally:
        await db.close()


async def build_main_menu_text(telegram_id: int, role: str | None) -> str:
    """Строит актуальную информационную панель главного меню.

    В PC-first профиле главное inline-меню одновременно служит лаконичной
    рабочей сводкой. Счётчики читаются заново при каждом вызове функции, поэтому
    /menu, нижняя кнопка «Меню» и любой возврат в main_menu показывают актуальные
    значения.
    """
    department = department_by_role(role)
    if not pc_ticket_workspace_enabled() or department not in {
        DEPARTMENT_CLIENT,
        DEPARTMENT_PURCHASING,
    }:
        prompt = await user_text(telegram_id, "main_menu_title")
        return f"🏠 <b>Главное меню</b>\n\n{prompt}"

    opposite = opposite_department(department)
    own_label = _DEPARTMENT_LABELS.get(department, "Наш отдел")
    opposite_label = _DEPARTMENT_LABELS.get(opposite, "Другой отдел")

    try:
        counts = await _load_dashboard_counts(department)
        unread = await count_unread_active_tickets(
            int(telegram_id),
            department,
            is_admin=False,
        )
    except Exception:
        logger.exception("Не удалось собрать сводку главного меню для %s", telegram_id)
        prompt = await user_text(telegram_id, "main_menu_title")
        return f"🏠 <b>Главное меню</b>\n\n{prompt}"

    lines = [
        "🏠 <b>Главное меню</b>",
        "",
        "📊 <b>Рабочая сводка</b>",
        (
            f"📥 <b>{own_label}</b>: "
            f"{counts['incoming_work']} в работе · {counts['incoming_new']} не обработано"
        ),
        (
            f"📤 <b>{own_label} → {opposite_label}</b>: "
            f"{counts['outgoing_work']} в работе · {counts['outgoing_new']} не обработано"
        ),
        (
            "⏰ <b>Просрочено (не сегодня)</b>: "
            f"к нам {counts['incoming_overdue']} · от нас {counts['outgoing_overdue']}"
        ),
    ]
    if int(unread or 0) > 0:
        lines.append(f"🔔 <b>Непрочитано лично вам:</b> {int(unread)}")

    return "\n".join(lines)
