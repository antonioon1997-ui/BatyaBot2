import aiosqlite
from app.config import settings


async def get_db():
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")
    return db


async def add_column_if_not_exists(db, table_name: str, column_name: str, column_definition: str):
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    columns = await cursor.fetchall()

    existing_columns = {column["name"] for column in columns}

    if column_name not in existing_columns:
        await db.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


async def init_db():
    db = await get_db()

    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            role TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT,
            processed_by INTEGER
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            order_number TEXT,
            order_status_snapshot TEXT,
            ticket_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            resolution TEXT,
            created_by INTEGER NOT NULL,
            requester_department TEXT NOT NULL,
            executor_department TEXT NOT NULL,
            taken_by INTEGER,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            excluded_from_stats INTEGER NOT NULL DEFAULT 0,
            admin_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            closed_at TEXT,
            reopened_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id)
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            user_id INTEGER,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            file_type TEXT NOT NULL,
            file_name TEXT,
            caption TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id)
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_telegram_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    await add_column_if_not_exists(db, "users", "deactivated_at", "TEXT")
    await add_column_if_not_exists(db, "users", "deactivated_by", "INTEGER")
    await add_column_if_not_exists(db, "users", "restored_at", "TEXT")
    await add_column_if_not_exists(db, "users", "restored_by", "INTEGER")

    await add_column_if_not_exists(db, "tickets", "deleted_at", "TEXT")
    await add_column_if_not_exists(db, "tickets", "deleted_by", "INTEGER")
    await add_column_if_not_exists(db, "tickets", "restored_at", "TEXT")
    await add_column_if_not_exists(db, "tickets", "restored_by", "INTEGER")


    await add_column_if_not_exists(db, "tickets", "priority", "TEXT NOT NULL DEFAULT 'normal'")
    await add_column_if_not_exists(db, "tickets", "category", "TEXT")
    await add_column_if_not_exists(db, "tickets", "auto_close_at", "TEXT")
    await add_column_if_not_exists(db, "tickets", "order_status_snapshot", "TEXT")
    await add_column_if_not_exists(db, "tickets", "assigned_at", "TEXT")
    await add_column_if_not_exists(db, "tickets", "assigned_by", "INTEGER")
    await add_column_if_not_exists(db, "tickets", "current_summary", "TEXT")
    await add_column_if_not_exists(db, "tickets", "next_action", "TEXT")
    await add_column_if_not_exists(db, "tickets", "summary_updated_at", "TEXT")
    await add_column_if_not_exists(db, "tickets", "summary_updated_by", "INTEGER")
    await add_column_if_not_exists(db, "tickets", "snoozed_until", "TEXT")
    await add_column_if_not_exists(db, "tickets", "snoozed_by", "INTEGER")

    await add_column_if_not_exists(db, "users", "day_off_start", "TEXT")
    await add_column_if_not_exists(db, "users", "day_off_end", "TEXT")
    await add_column_if_not_exists(db, "users", "day_off_set_by", "INTEGER")
    await add_column_if_not_exists(db, "users", "day_off_updated_at", "TEXT")
    await add_column_if_not_exists(db, "users", "message_style", "TEXT NOT NULL DEFAULT 'strict'")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            actor_telegram_id INTEGER,
            event_type TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_reads (
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_event_id INTEGER NOT NULL DEFAULT 0,
            read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticket_id, user_id),
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_transfer_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            requester_id INTEGER NOT NULL,
            current_assignee_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT,
            processed_by INTEGER,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_assignment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            from_user_id INTEGER,
            to_user_id INTEGER,
            actor_id INTEGER,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS day_off_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticket_id INTEGER NOT NULL,
            day_off_start TEXT NOT NULL,
            day_off_end TEXT NOT NULL,
            restored INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, ticket_id, day_off_start, day_off_end),
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS response_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS admin_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)


    await db.execute("""
        CREATE TABLE IF NOT EXISTS feedback_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            role TEXT,
            source TEXT NOT NULL,
            text TEXT,
            file_id TEXT,
            file_type TEXT,
            file_name TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            admin_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_type TEXT NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,
            none_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            choice_key TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            PRIMARY KEY (poll_id, user_id),
            FOREIGN KEY(poll_id) REFERENCES polls(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ticket_metrics (
            ticket_id INTEGER PRIMARY KEY,
            first_taken_at TEXT,
            first_response_at TEXT,
            first_completed_at TEXT,
            reopen_count INTEGER NOT NULL DEFAULT 0,
            assignment_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS ui_button_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT,
            department TEXT NOT NULL,
            button_id TEXT NOT NULL,
            button_text TEXT,
            source TEXT NOT NULL,
            scope TEXT NOT NULL,
            app_version TEXT,
            ui_version TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            stat_date TEXT PRIMARY KEY,
            total_open INTEGER NOT NULL DEFAULT 0,
            total_new INTEGER NOT NULL DEFAULT 0,
            total_in_work INTEGER NOT NULL DEFAULT 0,
            total_waiting INTEGER NOT NULL DEFAULT 0,
            total_snoozed INTEGER NOT NULL DEFAULT 0,
            total_unassigned INTEGER NOT NULL DEFAULT 0,
            created_today INTEGER NOT NULL DEFAULT 0,
            closed_today INTEGER NOT NULL DEFAULT 0,
            overdue_total INTEGER NOT NULL DEFAULT 0,
            collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary_dispatches (
            stat_date TEXT PRIMARY KEY,
            summary_text TEXT NOT NULL,
            sent_to_admin_at TEXT,
            sent_to_observers_at TEXT,
            confirmed_by INTEGER
        )
    """)

    await db.execute("""
        INSERT INTO ticket_metrics (ticket_id, first_completed_at, updated_at)
        SELECT id, CASE
            WHEN status = 'done' THEN COALESCE(closed_at, updated_at)
            WHEN status = 'waiting_confirmation' THEN updated_at
            ELSE NULL
        END, CURRENT_TIMESTAMP
        FROM tickets
        WHERE 1
        ON CONFLICT(ticket_id) DO NOTHING
    """)

    cursor = await db.execute("SELECT COUNT(*) AS count FROM response_templates WHERE department = 'purchasing'")
    row = await cursor.fetchone()
    if not row or int(row["count"] or 0) == 0:
        await db.executemany(
            """
            INSERT INTO response_templates (department, title, body, created_by)
            VALUES ('purchasing', ?, ?, NULL)
            """,
            [
                ("Товар заказан", "Товар заказан, ожидаем поступление."),
                ("Уточняем у поставщика", "Уточняем информацию у поставщика. Сообщим, когда получим ответ."),
                ("Готов к выдаче", "Заказ готов к выдаче."),
                ("Нужны данные", "Для продолжения работы нужны дополнительные данные по заказу."),
            ],
        )

    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_category ON tickets(category)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_auto_close_at ON tickets(auto_close_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_executor_status_deleted ON tickets(executor_department, status, is_deleted)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_creator_status_deleted ON tickets(created_by, status, is_deleted)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket_created ON ticket_comments(ticket_id, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_attachments_ticket_created ON ticket_attachments(ticket_id, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket_id ON ticket_events(ticket_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_users_active_role ON users(is_active, role)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_taken_status ON tickets(taken_by, status, is_deleted)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_order_open ON tickets(order_number, status, is_deleted)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_snoozed_until ON tickets(snoozed_until)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_reads_user_event ON ticket_reads(user_id, last_event_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_transfer_requests_ticket_status ON ticket_transfer_requests(ticket_id, status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_assignment_history_ticket ON ticket_assignment_history(ticket_id, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_day_off_releases_user_restored ON day_off_releases(user_id, restored)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_templates_department_active ON response_templates(department, is_active)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status_created ON feedback_messages(status, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_polls_status_created ON polls(status, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_poll_votes_poll_choice ON poll_votes(poll_id, choice_key)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ui_button_events_created ON ui_button_events(created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ui_button_events_button_created ON ui_button_events(button_id, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ui_button_events_user_created ON ui_button_events(user_id, created_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ui_button_events_department_created ON ui_button_events(department, created_at)")

    await db.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('reminder_time', ?)
    """, (settings.reminder_time,))

    await db.commit()
    await db.close()