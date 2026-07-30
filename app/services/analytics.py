from __future__ import annotations

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import get_db

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


async def note_ticket_created(ticket_id: int) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO ticket_metrics(ticket_id,updated_at) VALUES(?,CURRENT_TIMESTAMP) ON CONFLICT(ticket_id) DO NOTHING",
            (ticket_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def note_ticket_taken(ticket_id: int) -> None:
    db = await get_db()
    try:
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
    finally:
        await db.close()


async def note_first_executor_response(ticket_id: int) -> None:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO ticket_metrics(ticket_id,first_response_at,updated_at)
            VALUES(?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id) DO UPDATE SET
              first_response_at=COALESCE(ticket_metrics.first_response_at,CURRENT_TIMESTAMP),
              updated_at=CURRENT_TIMESTAMP
            """,
            (ticket_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def note_ticket_completed(ticket_id: int) -> None:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO ticket_metrics(ticket_id,first_completed_at,updated_at)
            VALUES(?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id) DO UPDATE SET
              first_completed_at=COALESCE(ticket_metrics.first_completed_at,CURRENT_TIMESTAMP),
              updated_at=CURRENT_TIMESTAMP
            """,
            (ticket_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def note_ticket_reopened(ticket_id: int) -> None:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO ticket_metrics(ticket_id,reopen_count,updated_at)
            VALUES(?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(ticket_id) DO UPDATE SET reopen_count=ticket_metrics.reopen_count+1,updated_at=CURRENT_TIMESTAMP
            """,
            (ticket_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def collect_daily_stats(stat_date: str | None = None):
    if stat_date is None:
        stat_date = datetime.now(MOSCOW_TZ).date().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
              SUM(CASE WHEN is_deleted=0 AND status NOT IN ('done','cancelled') THEN 1 ELSE 0 END) AS total_open,
              SUM(CASE WHEN is_deleted=0 AND status='new' THEN 1 ELSE 0 END) AS total_new,
              SUM(CASE WHEN is_deleted=0 AND status='in_work' THEN 1 ELSE 0 END) AS total_in_work,
              SUM(CASE WHEN is_deleted=0 AND status IN ('waiting_answer','waiting_confirmation') THEN 1 ELSE 0 END) AS total_waiting,
              SUM(CASE WHEN is_deleted=0 AND status NOT IN ('done','cancelled') AND snoozed_until IS NOT NULL AND snoozed_until>CURRENT_TIMESTAMP THEN 1 ELSE 0 END) AS total_snoozed,
              SUM(CASE WHEN is_deleted=0 AND status NOT IN ('done','cancelled') AND taken_by IS NULL THEN 1 ELSE 0 END) AS total_unassigned,
              SUM(CASE WHEN DATE(created_at,'+3 hours')=? THEN 1 ELSE 0 END) AS created_today,
              SUM(CASE WHEN closed_at IS NOT NULL AND DATE(closed_at,'+3 hours')=? THEN 1 ELSE 0 END) AS closed_today,
              SUM(CASE WHEN is_deleted=0 AND status NOT IN ('done','cancelled') AND created_at <= DATETIME('now','-2 days') THEN 1 ELSE 0 END) AS overdue_total
            FROM tickets
            """,
            (stat_date, stat_date),
        )
        row = await cursor.fetchone()
        values = [int(row[key] or 0) for key in (
            "total_open","total_new","total_in_work","total_waiting","total_snoozed","total_unassigned","created_today","closed_today","overdue_total"
        )]
        await db.execute(
            """
            INSERT INTO daily_stats(stat_date,total_open,total_new,total_in_work,total_waiting,total_snoozed,total_unassigned,created_today,closed_today,overdue_total,collected_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(stat_date) DO UPDATE SET
              total_open=excluded.total_open,total_new=excluded.total_new,total_in_work=excluded.total_in_work,
              total_waiting=excluded.total_waiting,total_snoozed=excluded.total_snoozed,total_unassigned=excluded.total_unassigned,
              created_today=excluded.created_today,closed_today=excluded.closed_today,overdue_total=excluded.overdue_total,
              collected_at=CURRENT_TIMESTAMP
            """,
            (stat_date, *values),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM daily_stats WHERE stat_date=?", (stat_date,))
        return await cursor.fetchone()
    finally:
        await db.close()


def build_daily_summary_text(row) -> str:
    date_text = datetime.strptime(row["stat_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
    return (
        f"📊 <b>Вечерняя сводка за {date_text}</b>\n\n"
        f"Создано сегодня: <b>{row['created_today']}</b>\n"
        f"Закрыто сегодня: <b>{row['closed_today']}</b>\n\n"
        f"Открыто всего: <b>{row['total_open']}</b>\n"
        f"Новых: {row['total_new']}\n"
        f"В работе: {row['total_in_work']}\n"
        f"Ожидают подтверждения/ответа: {row['total_waiting']}\n"
        f"Отложено закупкой: {row['total_snoozed']}\n"
        f"Без исполнителя: {row['total_unassigned']}\n"
        f"Открыты более двух дней: {row['overdue_total']}\n\n"
        "После проверки нажми кнопку, чтобы отправить эту же сводку наблюдателям."
    )


async def save_daily_summary(stat_date: str, text: str) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT sent_to_admin_at FROM daily_summary_dispatches WHERE stat_date=?", (stat_date,))
        existing = await cursor.fetchone()
        if existing and existing["sent_to_admin_at"]:
            return False
        await db.execute(
            """
            INSERT INTO daily_summary_dispatches(stat_date,summary_text)
            VALUES(?,?)
            ON CONFLICT(stat_date) DO UPDATE SET summary_text=excluded.summary_text
            """,
            (stat_date, text),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def mark_daily_summary_admin_sent(stat_date: str) -> None:
    db = await get_db()
    try:
        await db.execute(
            "UPDATE daily_summary_dispatches SET sent_to_admin_at=CURRENT_TIMESTAMP WHERE stat_date=?",
            (stat_date,),
        )
        await db.commit()
    finally:
        await db.close()


async def get_daily_summary(stat_date: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM daily_summary_dispatches WHERE stat_date=?", (stat_date,))
        return await cursor.fetchone()
    finally:
        await db.close()


async def mark_daily_summary_observers_sent(stat_date: str, admin_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            UPDATE daily_summary_dispatches SET sent_to_observers_at=CURRENT_TIMESTAMP,confirmed_by=?
            WHERE stat_date=? AND sent_to_observers_at IS NULL
            """,
            (admin_id, stat_date),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def export_statistics_csv() -> bytes:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT t.id,t.order_number,t.requester_department,t.executor_department,t.status,t.priority,t.category,
                   t.created_by,t.taken_by,t.created_at,t.closed_at,
                   m.first_taken_at,m.first_response_at,m.first_completed_at,m.reopen_count,m.assignment_count
            FROM tickets t LEFT JOIN ticket_metrics m ON m.ticket_id=t.id
            WHERE t.is_deleted=0 ORDER BY t.id
            """
        )
        tickets = await cursor.fetchall()
        cursor = await db.execute("SELECT * FROM daily_stats ORDER BY stat_date")
        daily = await cursor.fetchall()
    finally:
        await db.close()

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ТИКЕТЫ"])
    writer.writerow([
        "id","order_number","requester_department","executor_department","status","priority","category",
        "created_by","taken_by","created_at_utc","closed_at_utc","first_taken_at_utc","first_response_at_utc",
        "first_completed_at_utc","reopen_count","assignment_count"
    ])
    for row in tickets:
        writer.writerow([row[key] for key in row.keys()])
    writer.writerow([])
    writer.writerow(["ЕЖЕДНЕВНЫЕ СРЕЗЫ"])
    if daily:
        writer.writerow(list(daily[0].keys()))
        for row in daily:
            writer.writerow([row[key] for key in row.keys()])
    return ("\ufeff" + output.getvalue()).encode("utf-8")
