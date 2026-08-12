from __future__ import annotations

import asyncio
import os
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _prepare_legacy_database(database_path: Path) -> None:
    """Создаёт минимальную схему старой версии и одну запись для проверки миграции."""
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                order_number TEXT,
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
            """
        )
        connection.execute(
            """
            INSERT INTO tickets (
                id, title, description, ticket_type, direction, status, created_by,
                requester_department, executor_department, is_deleted, excluded_from_stats
            ) VALUES (900, 'Старый тикет', 'Данные должны сохраниться', 'task',
                      'client_to_purchasing', 'new', 1001, 'client', 'purchasing', 0, 0)
            """
        )
        connection.commit()


async def _prepare_users(database_path: Path) -> None:
    import aiosqlite

    async with aiosqlite.connect(database_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executemany(
            """
            INSERT INTO users (telegram_id, username, full_name, role, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            [
                (1001, "client_one", "Клиент <Один>", "client"),
                (2001, "buyer_one", "Закупщик & Один", "purchaser"),
                (2002, "buyer_two", "Закупщик Два", "purchasing"),
            ],
        )
        await db.commit()


async def run_selftest() -> None:
    if os.getenv("BATYABOT_SELFTEST") != "1":
        raise RuntimeError("Самотест разрешён только в изолированном режиме обновлятора")

    database_path = Path(os.environ["DATABASE_PATH"]).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.unlink(missing_ok=True)

    from app.database import init_db
    from app.handlers.tickets.utils import (
        can_participant_cancel_ticket,
        can_user_return_ticket,
        extract_order_number,
        notify_department_about_ticket,
    )
    from app.handlers.tickets.workspace import _current_order_block
    from app.keyboards.admin import admin_menu
    from app.keyboards.common import bottom_menu_for_role, main_menu_for_role, ticket_work_menu_keyboard
    from app.keyboards.productivity import work_hub_keyboard
    from app.keyboards.tickets import (
        post_create_options_keyboard,
        ticket_action_keyboard,
        ticket_notification_keyboard,
        ticket_workspace_keyboard,
        workspace_ticket_action_keyboard,
    )
    from app.services.analytics import collect_daily_stats, export_statistics_csv
    from app.services.attachments import create_attachment
    from app.services.backups import create_database_backup
    from app.services.feedback import create_feedback, get_feedback, list_feedback
    from app.services.polls import create_poll, get_poll_results, upsert_vote
    from app.services.preferences import get_message_style, set_message_style
    from app.services.project_export import create_project_export
    from app.services.ticket_messages import get_ticket_message_ids, send_live_ticket_text, set_ticket_message_ids
    from app.services.ui_context import get_ui_context, set_ticket_list_context
    from app.services.ui_messages import get_ui_message_ids, send_ui_text, set_ui_message_ids
    from app.services.ui_metrics import (
        classify_callback_button,
        classify_reply_button,
        export_ui_metrics_csv,
        get_button_summary,
        get_department_totals,
        get_unused_main_buttons,
        record_ui_event,
    )
    from app.services.ui_versions import (
        CURRENT_UI_ID,
        LEGACY_UI_ID,
        activate_ui_version,
        ensure_ui_versions,
        get_active_ui_id,
        help_button_enabled,
        list_ui_versions,
        pc_ticket_workspace_enabled,
    )
    from app.services.templates import create_response_template, get_response_templates, update_response_template
    from app.services.main_menu_dashboard import build_main_menu_text
    from app.services.order_status import (
        build_order_status_index,
        build_purchasing_snapshot,
        extract_order_number_from_query,
        format_purchasing_lines,
        get_order_status,
    )
    from app.services.tickets import (
        add_ticket_comment,
        close_due_auto_close_ticket,
        count_tickets_for_department_reminder,
        create_ticket,
        get_active_users_by_department,
        get_archive_incoming_tickets,
        get_ticket_by_id,
        get_ticket_events,
        search_archive_tickets,
        schedule_ticket_auto_close,
        set_ticket_category,
        set_ticket_priority,
        take_ticket,
        update_ticket_status,
    )
    from app.services.update_manager import predict_next_version
    from app.services.work_management import (
        assign_ticket,
        clear_day_off,
        create_transfer_request,
        expire_finished_day_offs,
        find_open_duplicates,
        get_assigned_tickets,
        get_common_tickets,
        get_due_snoozed_tickets,
        get_unread_active_tickets,
        mark_ticket_read,
        process_transfer_request,
        restore_day_off_tickets,
        search_active_tickets,
        set_day_off,
        set_ticket_summary,
        snooze_ticket,
        wake_snoozed_ticket,
    )
    from app.utils import format_moscow_datetime, html_escape

    # Импорт всех роутеров ловит ошибки после рефакторинга импортов до установки.
    from app.handlers import admin, admin_feedback, admin_productivity, admin_ui_metrics, help, start, system, tickets, updater  # noqa: F401

    _prepare_legacy_database(database_path)
    _assert(predict_next_version("2.6.0", "patch") == "2.6.1", "Patch SemVer рассчитывается неверно")
    _assert(predict_next_version("2.6.9", "minor") == "2.7.0", "Minor SemVer рассчитывается неверно")
    _assert(predict_next_version("2.9.9", "major") == "3.0.0", "Major SemVer рассчитывается неверно")

    await init_db()
    await _prepare_users(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tickets)")}
        _assert("auto_close_at" in columns, "Миграция auto_close_at не выполнена")
        _assert("order_status_snapshot" in columns, "Миграция order_status_snapshot не выполнена")
        for required_column in ("assigned_at", "assigned_by", "current_summary", "next_action", "snoozed_until"):
            _assert(required_column in columns, f"Миграция {required_column} не выполнена")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        _assert("message_style" in user_columns, "Миграция message_style не выполнена")
        for required_table in (
            "ticket_reads", "ticket_transfer_requests", "ticket_assignment_history",
            "day_off_releases", "response_templates", "ticket_metrics", "daily_stats",
            "feedback_messages", "polls", "poll_votes", "admin_notes", "ui_button_events",
            "ticket_message_registry", "ui_message_registry", "ui_navigation_state",
        ):
            _assert(required_table in tables, f"Не создана таблица {required_table}")
        template_count = connection.execute(
            "SELECT COUNT(*) FROM response_templates WHERE department='purchasing' AND is_active=1"
        ).fetchone()[0]
        _assert(template_count >= 4, "Не созданы стартовые шаблоны закупки")
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(tickets)")}
        _assert("idx_tickets_executor_status_deleted" in indexes, "Не создан составной индекс тикетов")
        legacy = connection.execute(
            "SELECT title, priority, category, auto_close_at, order_status_snapshot FROM tickets WHERE id = 900"
        ).fetchone()
        _assert(legacy is not None and legacy[0] == "Старый тикет", "Миграция потеряла старый тикет")
        _assert(
            legacy[1] == "normal"
            and legacy[2] is None
            and legacy[3] is None
            and legacy[4] is None,
            "Новые поля старого тикета заполнены неверно",
        )

    # Центр помощи, персональный стиль, обратная связь и голосования.
    ensure_ui_versions()
    _assert(get_active_ui_id() == CURRENT_UI_ID, "Новая версия интерфейса не активирована по умолчанию")
    _assert(help_button_enabled(), "В текущем интерфейсе скрыта кнопка помощи")
    _assert(pc_ticket_workspace_enabled(), "PC-first workspace не включён в текущем UI-профиле")
    _assert(len(list_ui_versions()) <= 5, "Хранится больше пяти версий интерфейса")
    activate_ui_version(LEGACY_UI_ID)
    _assert(not help_button_enabled(), "Классический интерфейс не скрывает центр помощи")
    _assert(not pc_ticket_workspace_enabled(), "Откат UI не отключает PC-first workspace")
    activate_ui_version(CURRENT_UI_ID)
    _assert(pc_ticket_workspace_enabled(), "PC-first workspace не восстановился после возврата UI-профиля")

    await set_ticket_list_context(
        1001,
        list_type="incoming",
        page=2,
        queue_ids=[101, 102, 103],
        filters={"status": "new"},
        mode="normal",
    )
    ui_context = await get_ui_context(1001)
    _assert(ui_context.list_type == "incoming" and ui_context.page == 2, "UI context не сохраняет список/страницу")
    _assert(ui_context.queue == [101, 102, 103], "UI context не сохраняет очередь тикетов")
    _assert(ui_context.filters_dict.get("status") == "new", "UI context не сохраняет фильтр")

    _assert(await set_message_style(1001, "friendly") == "friendly", "Не сохраняется дружелюбный стиль")
    _assert(await get_message_style(1001) == "friendly", "Дружелюбный стиль не читается")
    _assert(await set_message_style(1001, "strict") == "strict", "Не возвращается строгий стиль")

    feedback_id = await create_feedback(
        user_id=1001,
        username="client_one",
        full_name="Клиент <Один>",
        role="client",
        source="idea",
        text="Было бы удобно сократить один шаг",
    )
    _assert((await get_feedback(feedback_id))["status"] == "new", "Обратная связь создана с неверным статусом")
    _assert(any(int(row["id"]) == feedback_id for row in await list_feedback(status="new")), "Новое сообщение не попало в список")

    poll_id = await create_poll(
        poll_type="choice",
        question="Какой вариант удобнее?",
        options=["Вариант А", "Вариант Б"],
        none_label="🚫 Оставить как есть",
        created_by=1,
    )
    _assert(await upsert_vote(poll_id, 1001, "0"), "Голос не сохранён")
    poll_result = await get_poll_results(poll_id)
    _assert(poll_result["total"] == 1 and poll_result["counts"].get("0") == 1, "Результат голосования неверен")

    # Метрики кнопок: reply- и inline-варианты объединяются под стабильными ID.
    reply_info = classify_reply_button("📌 Моя работа")
    callback_info = classify_callback_button("work_hub", "📌 Моя работа")
    _assert(reply_info and callback_info and reply_info[0] == callback_info[0] == "main.my_work", "Кнопка «Моя работа» классифицируется по-разному")
    _assert(
        await record_ui_event(
            user_id=1001,
            button_id=reply_info[0],
            button_text=reply_info[1],
            source="reply",
            scope=reply_info[2],
        ),
        "Метрика reply-кнопки не сохранена",
    )
    _assert(
        await record_ui_event(
            user_id=2001,
            button_id="main.incoming",
            button_text="📥 Входящие",
            source="inline",
            scope="main",
        ),
        "Метрика inline-кнопки не сохранена",
    )
    metric_rows = await get_button_summary(days=7, scope="main")
    _assert(any(row["button_id"] == "main.my_work" for row in metric_rows), "Метрика не попала в общую сводку")
    department_rows = await get_department_totals(days=7, scope="main")
    _assert({row["department"] for row in department_rows} >= {"client", "purchasing"}, "Метрики не разделились по отделам")
    unused = await get_unused_main_buttons(days=7)
    _assert(any(button_id == "main.archive" for button_id, _ in unused), "Не определяются кнопки без нажатий")
    metrics_csv = await export_ui_metrics_csv(days=7)
    _assert(b"button_id" in metrics_csv and b"main.my_work" in metrics_csv, "CSV метрик не сформирован")

    # Обычный ответ запускает работу, но назначение исполнителя остаётся добровольным.
    common_ticket = await create_ticket(
        title="Общий тикет",
        description="Ответ не должен автоматически назначать сотрудника",
        order_number="19876",
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
    )
    await add_ticket_comment(common_ticket, 2001, "Ответ без назначения", start_work_if_new=True)
    common_after_reply = await get_ticket_by_id(common_ticket)
    _assert(common_after_reply["status"] == "in_work", "Ответ не перевёл новый тикет в работу")
    _assert(common_after_reply["taken_by"] is None, "Ответ ошибочно назначил исполнителя")
    _assert(common_ticket in [int(row["id"]) for row in await get_common_tickets(2001, "purchasing")], "Общий тикет пропал из списка отдела")

    # Новая система назначения действительно защищена от двух одновременных захватов.
    assignment_ticket = await create_ticket(
        title="Добровольное назначение",
        description="Проверка атомарного назначения",
        order_number=None,
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
    )
    assignment_results = await asyncio.gather(
        assign_ticket(assignment_ticket, 2001, 2001, expected_assignee=None, reason="self_assignment"),
        assign_ticket(assignment_ticket, 2002, 2002, expected_assignee=None, reason="self_assignment"),
    )
    _assert(sum(bool(value) for value in assignment_results) == 1, "Атомарное назначение допускает двух победителей")
    assignment_row = await get_ticket_by_id(assignment_ticket)
    current_assignee = int(assignment_row["taken_by"])
    requester = 2002 if current_assignee == 2001 else 2001
    _assert(assignment_ticket in [int(row["id"]) for row in await get_assigned_tickets(current_assignee)], "Назначенный тикет не попал в личный список")

    request_id = await create_transfer_request(assignment_ticket, requester)
    _assert(bool(request_id), "Не создан запрос передачи тикета")
    processed, transferred_ticket, transferred_to = await process_transfer_request(request_id, current_assignee, True)
    _assert(processed and transferred_ticket == assignment_ticket and transferred_to == requester, "Запрос передачи обработан неверно")
    _assert(int((await get_ticket_by_id(assignment_ticket))["taken_by"]) == requester, "Тикет не передан запросившему сотруднику")

    await add_ticket_comment(assignment_ticket, 1001, "Новая информация для исполнителя")
    unread_ids = [int(row["id"]) for row in await get_unread_active_tickets(requester, "purchasing")]
    _assert(assignment_ticket in unread_ids, "Новая запись не отмечена непрочитанной")
    await mark_ticket_read(assignment_ticket, requester)
    unread_ids = [int(row["id"]) for row in await get_unread_active_tickets(requester, "purchasing")]
    _assert(assignment_ticket not in unread_ids, "Просмотр не снял признак непрочитанного")
    _assert(assignment_ticket in [int(row["id"]) for row in await search_active_tickets(str(assignment_ticket), requester, "purchasing")], "Поиск по номеру активного тикета не работает")
    _assert(common_ticket in [int(row["id"]) for row in await find_open_duplicates("19876")], "Не найден открытый дубль по заказу")

    _assert(await set_ticket_summary(assignment_ticket, requester, current_summary="Поставщик подтвердил"), "Не сохранён краткий итог")
    _assert(await set_ticket_summary(assignment_ticket, requester, next_action="Проверить завтра"), "Не сохранено следующее действие")
    summary_ticket = await get_ticket_by_id(assignment_ticket)
    _assert(summary_ticket["current_summary"] and summary_ticket["next_action"], "Рабочая сводка тикета не читается")

    moscow = ZoneInfo("Europe/Moscow")
    _assert(await snooze_ticket(assignment_ticket, requester, datetime.now(moscow) + timedelta(hours=1)), "Не удалось отложить тикет закупки")
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE tickets SET snoozed_until=DATETIME('now','-1 minute') WHERE id=?", (assignment_ticket,))
        connection.commit()
    _assert(assignment_ticket in [int(row["id"]) for row in await get_due_snoozed_tickets()], "Просроченное отложение не найдено")
    _assert(await wake_snoozed_ticket(assignment_ticket), "Отложенный тикет не вернулся в работу")

    _, _, released = await set_day_off(requester, 0, 1, requester)
    _assert(assignment_ticket in released, "Выходной не вернул назначенный тикет в общий список")
    candidates = await clear_day_off(requester, requester)
    _assert(assignment_ticket in candidates, "После отмены выходного не предложено восстановление")
    restored = await restore_day_off_tickets(requester, requester)
    _assert(assignment_ticket in restored, "Тикет не восстановлен прежнему исполнителю")

    # Автоматическое завершение периода выходных также предлагает безопасное восстановление.
    _, _, released = await set_day_off(requester, 0, 1, requester)
    _assert(assignment_ticket in released, "Повторный выходной не освободил тикет")
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE users SET day_off_start='2020-01-01',day_off_end='2020-01-01' WHERE telegram_id=?", (requester,))
        connection.execute("UPDATE day_off_releases SET day_off_start='2020-01-01',day_off_end='2020-01-01' WHERE user_id=? AND restored=0", (requester,))
        connection.commit()
    finished = await expire_finished_day_offs()
    _assert(any(user_id == requester and assignment_ticket in ids for user_id, ids in finished), "Завершившийся выходной не обработан")
    _assert(assignment_ticket in await restore_day_off_tickets(requester, requester), "После завершения выходного тикет не восстановлен")

    templates = await get_response_templates("purchasing")
    _assert(len(templates) >= 4, "Шаблоны закупки недоступны")
    template_id = await create_response_template("Самотест", "Тестовый ответ", 1)
    _assert(await update_response_template(template_id, body="Изменённый тестовый ответ"), "Шаблон нельзя редактировать")

    race_ticket = await create_ticket(
        title="Проверка гонки",
        description="Два сотрудника нажимают одновременно",
        order_number="11208",
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
    )
    first_take = await take_ticket(race_ticket, 2001)
    second_take = await take_ticket(race_ticket, 2002)
    _assert(first_take and not second_take, "Повторное взятие уже занятого тикета не заблокировано")

    reminder_new_ticket = await create_ticket(
        title="Новое напоминание",
        description="Тикет должен попасть в категорию не в работе",
        order_number=None,
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
    )
    _assert(reminder_new_ticket > 0, "Не создан тестовый тикет напоминания")
    _assert(
        await count_tickets_for_department_reminder("purchasing", "new") >= 1,
        "Напоминания не считают новые тикеты закупки",
    )
    _assert(
        await count_tickets_for_department_reminder("purchasing", "work") >= 1,
        "Напоминания не считают тикеты в работе",
    )
    purchasing_users = await get_active_users_by_department("purchasing")
    _assert(
        {int(user["telegram_id"]) for user in purchasing_users} == {2001, 2002},
        "Получатели напоминаний отдела определены неверно",
    )

    delayed_ticket = await create_ticket(
        title="Отложенное закрытие",
        description="Проверка таймера",
        order_number=None,
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
    )
    _assert(await take_ticket(delayed_ticket, 2001), "Не удалось взять новый тикет")
    _assert(
        await schedule_ticket_auto_close(
            delayed_ticket,
            2001,
            minutes=10,
            comment="Исполнитель пометил тикет выполненным.",
        ),
        "Не удалось назначить автозакрытие",
    )
    before = await get_ticket_by_id(delayed_ticket)
    original_deadline = before["auto_close_at"]
    _assert(before["status"] == "waiting_confirmation" and original_deadline, "Не сохранён срок автозакрытия")

    await set_ticket_priority(delayed_ticket, "urgent", 2001)
    await set_ticket_category(delayed_ticket, "task", 2001)
    after_metadata = await get_ticket_by_id(delayed_ticket)
    _assert(after_metadata["auto_close_at"] == original_deadline, "Приоритет или категория сбросили таймер")

    cancelled = await add_ticket_comment(
        delayed_ticket,
        1001,
        "Нужно уточнить <детали> & сроки",
        cancel_auto_close=True,
    )
    _assert(cancelled, "Новый комментарий не отменил автозакрытие")
    after_comment = await get_ticket_by_id(delayed_ticket)
    _assert(after_comment["status"] == "in_work" and after_comment["auto_close_at"] is None, "Тикет не вернулся в работу")

    _assert(await schedule_ticket_auto_close(delayed_ticket, 2001, minutes=1), "Повторный таймер не назначен")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE tickets SET auto_close_at = DATETIME('now', '-1 minute') WHERE id = ?",
            (delayed_ticket,),
        )
        connection.commit()
    close_results = await asyncio.gather(
        close_due_auto_close_ticket(delayed_ticket),
        close_due_auto_close_ticket(delayed_ticket),
    )
    _assert(sum(bool(value) for value in close_results) == 1, "Автозакрытие выполнилось повторно")

    # Регрессия архива: обычное открытие входящего архива не должно зависеть
    # от переменной поискового запроса ticket_id. Эта проверка ловит ошибку
    # NameError, которая появилась при добавлении поиска архива по номеру тикета.
    incoming_archive = await get_archive_incoming_tickets("purchasing", limit=None)
    _assert(
        delayed_ticket in [int(row["id"]) for row in incoming_archive],
        "Закрытый тикет не попал в архив входящих закупки",
    )

    archive_search = await search_archive_tickets(
        str(delayed_ticket),
        telegram_id=1001,
        department="client",
        is_observer=False,
        is_admin=False,
        limit=200,
    )
    _assert(archive_search, "Поиск архива по номеру тикета ничего не вернул")
    _assert(
        int(archive_search[0]["id"]) == delayed_ticket,
        "Точное совпадение номера тикета не стоит первым в поиске архива",
    )

    confirmation_ticket = await create_ticket(
        title="Двухэтапное подтверждение",
        description="Старое направление должно сохраниться",
        order_number=None,
        created_by=2001,
        requester_department="purchasing",
        executor_department="client",
    )
    changed = await update_ticket_status(
        confirmation_ticket,
        "waiting_confirmation",
        actor_telegram_id=1001,
        expected_statuses=("new", "in_work"),
    )
    _assert(changed, "Не работает двухэтапное подтверждение закупка → клиентский отдел")
    confirmation = await get_ticket_by_id(confirmation_ticket)
    _assert(confirmation["auto_close_at"] is None, "Старому направлению ошибочно назначен таймер")

    client_user = {"telegram_id": 1001, "role": "client"}
    closed_ticket = dict(confirmation)
    closed_ticket["status"] = "done"
    _assert(can_user_return_ticket(closed_ticket, client_user), "Потеряно право клиентского отдела вернуть закрытый тикет")
    callbacks = [
        button.callback_data
        for row in ticket_action_keyboard(closed_ticket, client_user, False).inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]
    _assert(
        f"ticket_return:{confirmation_ticket}" in callbacks,
        "В закрытом тикете пропала кнопка возврата в работу",
    )

    open_ticket = dict(await get_ticket_by_id(confirmation_ticket))
    open_ticket["status"] = "in_work"
    author_user = {"telegram_id": 2001, "role": "purchasing"}
    colleague_user = {"telegram_id": 2002, "role": "purchasing"}
    _assert(can_participant_cancel_ticket(open_ticket, author_user), "Автор не может закрыть тикет как неактуальный")
    _assert(not can_participant_cancel_ticket(open_ticket, colleague_user), "Чужой сотрудник может закрыть тикет автора")
    _assert(can_participant_cancel_ticket(open_ticket, colleague_user, True), "Администратор не может закрыть тикет")

    notification_callbacks = [
        button.callback_data
        for row in ticket_notification_keyboard(open_ticket, author_user).inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]
    _assert(
        notification_callbacks[0] == f"ticket_cancel:{confirmation_ticket}",
        "В уведомлении автора кнопка закрытия не вынесена наверх",
    )
    post_create_callbacks = [
        button.callback_data
        for row in post_create_options_keyboard(confirmation_ticket).inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]
    _assert(
        f"ticket_cancel:{confirmation_ticket}" in post_create_callbacks,
        "В сообщении после создания нет быстрой кнопки закрытия",
    )

    events = await get_ticket_events(delayed_ticket)
    event_types = {event["event_type"] for event in events}
    _assert({"created", "taken", "auto_close_scheduled", "comment", "auto_closed"} <= event_types, "История ключевых действий неполна")

    _assert(extract_order_number("11208 заказать товар") == "11208", "Не распознан номер заказа")
    _assert(extract_order_number("21208 заказать товар") is None, "Ложно распознан номер заказа")
    _assert(extract_order_number_from_query("Заказ №11786") == "11786", "Поисковый номер заказа не распознан")

    sheet_index = build_order_status_index([
        ["Номер заказа", "Статус МС", "SKU", "Статусы", "Поставщик", "Заказы поставщика"],
        [
            "11786",
            "[O] Ожидание товара",
            "14163, 1 шт\n28599, 1 шт",
            "14163, 1 шт, Заказ доставляется\n28599, 1 шт, Заказ доставляется",
            "Привоз\nПЗ",
            "14163, 1 шт, Привоз, заказ 388776, Заказ доставляется\n"
            "28599, 1 шт, ПЗ, заказ 119861/245415, Заказ доставляется",
        ],
        [
            "11840",
            "[N] Принят",
            "4205, 2 шт",
            "4205, 2 шт, Не просчитан",
            "",
            "4205, 2 шт, Не просчитан",
        ],
    ])
    _assert(sheet_index["11786"].client_items[0].startswith("14163"), "Строки клиентского статуса потеряны")
    _assert(
        sheet_index["11786"].purchasing_items == (
            "14163, 1 шт, заказ 388776, Заказ доставляется",
            "28599, 1 шт, заказ 119861/245415, Заказ доставляется",
        ),
        "Поставщики или номера заказов обработаны неверно",
    )
    _assert(
        "номер заказа поставщика не указан" in sheet_index["11840"].purchasing_items[0],
        "Отсутствующий номер заказа поставщика не обозначен",
    )
    _assert(
        format_purchasing_lines(
            "9073, Не оплачено, ПЗ, заказ 119551/245244, Заказ доставляется",
            "ПЗ",
        )[0] == "9073, Не оплачено, заказ 119551/245244, Заказ доставляется",
        "Нестандартное количество или номер ПЗ обработаны неверно",
    )
    incoming_snapshot = build_purchasing_snapshot(sheet_index["11786"])
    _assert(
        "Статус МС: [O] Ожидание товара" in incoming_snapshot
        and "14163, 1 шт, заказ 388776, Заказ доставляется" in incoming_snapshot
        and "28599, 1 шт, заказ 119861/245415, Заказ доставляется" in incoming_snapshot,
        "Снимок заказа для входящего тикета сформирован неполно",
    )

    # Проверяем новый read-only источник OrderExporter без Google Sheets.
    order_db_path = database_path.with_name("orderexporter-selftest.db")
    order_db_path.unlink(missing_ok=True)
    with sqlite3.connect(order_db_path) as order_db:
        order_db.executescript(
            """
            CREATE TABLE sync_runs (
                id INTEGER PRIMARY KEY, finished_at TEXT, status TEXT, app_version TEXT
            );
            CREATE TABLE moysklad_orders (
                ms_order_id TEXT PRIMARY KEY, bs_order_number TEXT, status_name TEXT,
                active_in_selection INTEGER, updated_at_ms TEXT
            );
            CREATE TABLE moysklad_order_items (
                ms_position_id TEXT PRIMARY KEY, ms_order_id TEXT, sku_raw TEXT,
                sku_normalized TEXT, quantity_text TEXT, name TEXT, active INTEGER
            );
            CREATE TABLE current_supplier_statuses (
                provider TEXT, external_order_id TEXT, sku_raw TEXT, sku_normalized TEXT,
                quantity_text TEXT, source_quantity_text TEXT, effective_quantity_text TEXT,
                raw_status TEXT, normalized_status TEXT, bs_order_number TEXT
            );
            CREATE TABLE supplier_status_current (
                provider TEXT, external_order_id TEXT, raw_status TEXT, normalized_status TEXT,
                ready_detected_at TEXT, issued_detected_at TEXT
            );
            CREATE TABLE supplier_orders (
                id INTEGER PRIMARY KEY, provider TEXT, external_order_id TEXT
            );
            CREATE TABLE supplier_order_state (
                supplier_order_id INTEGER, bs_order_number TEXT, quantity_text TEXT, active INTEGER
            );
            """
        )
        order_db.execute(
            "INSERT INTO sync_runs VALUES (1, ?, 'SUCCESS', '2.7.1-beta')",
            (datetime.now(ZoneInfo("UTC")).isoformat(),),
        )
        order_db.execute(
            "INSERT INTO moysklad_orders VALUES ('ms1', '11786', '[O] Ожидание товара', 1, '')"
        )
        order_db.executemany(
            "INSERT INTO moysklad_order_items VALUES (?, 'ms1', ?, ?, ?, ?, 1)",
            [
                ('p1', '14163', '14163', '1', 'Товар 1'),
                ('p2', '28599', '28599', '2', 'Товар 2'),
                ('p3', '0288', '288', '1', 'Товар с ведущим нулём'),
            ],
        )
        order_db.executemany(
            "INSERT INTO current_supplier_statuses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '11786')",
            [
                # В live-data остаток 1, но заказу BS назначено 2: основная строка должна показать 2.
                ('privoz', '388776', '14163', '14163', '2', '2', '1', 'Выдан частично', 'ready'),
                ('pozakupy', '119861/245415', '28599', '28599', '2', '2', '2', 'На складе', 'ready'),
                ('privoz', '399999', '288', '288', '1', '1', '1', 'Выдан со склада', 'issued'),
                # Старая связь остаётся видимой во view, но active=0 в supplier_order_state — не показывать.
                ('privoz', '399998', '288', '288', '9', '9', '9', 'Выдан со склада', 'issued'),
            ],
        )
        order_db.executemany(
            "INSERT INTO supplier_status_current VALUES (?, ?, ?, ?, ?, ?)",
            [
                ('privoz', '388776', 'Выдан частично', 'ready', '2026-08-08T14:45:00+00:00', None),
                ('pozakupy', '119861/245415', 'На складе', 'ready', '2026-08-08T17:45:00+03:00', None),
                ('privoz', '399999', 'Выдан со склада', 'issued', '2026-08-08T12:00:00+00:00', '2026-08-09T10:00:00+00:00'),
                ('privoz', '399998', 'Выдан со склада', 'issued', '2026-08-07T12:00:00+00:00', '2026-08-08T10:00:00+00:00'),
            ],
        )
        order_db.executemany(
            "INSERT INTO supplier_orders(id, provider, external_order_id) VALUES (?, ?, ?)",
            [
                (1, 'privoz', '388776'),
                (2, 'pozakupy', '119861/245415'),
                (3, 'privoz', '399999'),
                (4, 'privoz', '399998'),
            ],
        )
        order_db.executemany(
            "INSERT INTO supplier_order_state(supplier_order_id, bs_order_number, quantity_text, active) VALUES (?, '11786', ?, ?)",
            [
                (1, '2', 1),
                (2, '2', 1),
                (3, '1', 1),
                (4, '9', 0),
            ],
        )
        order_db.commit()

    from app.config import settings as runtime_settings
    runtime_settings.order_database_path = str(order_db_path)
    runtime_settings.order_database_stale_after_seconds = 86400
    order_lookup = await get_order_status("11786")
    _assert(order_lookup.record is not None, "OrderExporter не вернул существующий BS")
    _assert(
        "14163, 2 шт, заказ 388776, Выдан частично — Приход на склад 08.08.2026 17:45 (остаток у поставщика: 1 шт)"
        in order_lookup.record.purchasing_items
        and "28599, 2 шт, заказ 119861/245415, На складе — Приход на склад 08.08.2026 17:45" in order_lookup.record.purchasing_items
        and "0288, 1 шт, заказ 399999, Выдан со склада — дата выдачи 09.08.2026 13:00" in order_lookup.record.purchasing_items,
        "Карточка поставщиков из OrderExporter сформирована неверно или потеряны даты прихода/выдачи",
    )
    _assert(
        any("Выдан со склада — дата выдачи 09.08.2026 13:00" in line for line in order_lookup.record.client_items)
        and any("Приход на склад 08.08.2026 17:45" in line for line in order_lookup.record.client_items),
        "Даты прихода/выдачи не попали в клиентский блок проверки заказа",
    )
    _assert(
        all("399998" not in line for line in order_lookup.record.purchasing_items),
        "Неактивное назначение supplier_order_state.active=0 попало в текущие статусы",
    )

    workspace_order_block = await _current_order_block(
        {"order_number": "11786"},
        {"role": "purchasing"},
        False,
    )
    _assert(
        workspace_order_block
        and "Статусы заказов поставщиков:" in workspace_order_block
        and "119861/245415" in workspace_order_block
        and "Приход на склад 08.08.2026 17:45" in workspace_order_block,
        "Workspace-карточка потеряла актуальные статусы заказов поставщиков",
    )

    snapshot_ticket = await create_ticket(
        title="Вопрос из статуса заказа",
        description="Когда будет поставка?",
        order_number="11786",
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
        order_status_snapshot="Статус МС: тест\nСтатусы заказов поставщиков:\n14163, заказ 388776",
    )
    _assert(
        (await get_ticket_by_id(snapshot_ticket))["order_status_snapshot"],
        "Снимок статуса заказа не сохранился в тикете",
    )

    await set_ticket_message_ids(snapshot_ticket, 1001, [101, 102, 102])
    _assert(
        await get_ticket_message_ids(snapshot_ticket, 1001) == [101, 102],
        "Реестр живых карточек тикета сохраняет сообщения неверно",
    )

    class _FakeSentMessage:
        def __init__(self, message_id: int):
            self.message_id = message_id

    class _FakeBot:
        def __init__(self):
            self.sent: list[tuple[int, str]] = []
            self.media: list[tuple[str, int, str, str]] = []
            self.deleted: list[tuple[int, int]] = []
            self.next_message_id = 500

        async def send_message(self, *, chat_id: int, text: str, reply_markup=None):
            self.sent.append((int(chat_id), str(text)))
            self.next_message_id += 1
            return _FakeSentMessage(self.next_message_id)

        async def _send_media(self, kind: str, chat_id: int, file_id: str, caption: str = "", reply_markup=None):
            self.media.append((kind, int(chat_id), str(file_id), str(caption or "")))
            self.next_message_id += 1
            return _FakeSentMessage(self.next_message_id)

        async def send_photo(self, *, chat_id: int, photo: str, caption: str = "", reply_markup=None):
            return await self._send_media("photo", chat_id, photo, caption, reply_markup)

        async def send_document(self, *, chat_id: int, document: str, caption: str = "", reply_markup=None):
            return await self._send_media("document", chat_id, document, caption, reply_markup)

        async def send_video(self, *, chat_id: int, video: str, caption: str = "", reply_markup=None):
            return await self._send_media("video", chat_id, video, caption, reply_markup)

        async def delete_message(self, *, chat_id: int, message_id: int):
            self.deleted.append((int(chat_id), int(message_id)))

        async def edit_message_text(self, *, chat_id: int, message_id: int, text: str, reply_markup=None):
            if not hasattr(self, "edited"):
                self.edited = []
            self.edited.append((int(chat_id), int(message_id), str(text)))
            return _FakeSentMessage(message_id)

    # Первое уведомление о новом входящем тикете должно использовать тот же
    # медиарендерер, что и ручное открытие карточки: фото является карточкой,
    # а не отдельным сообщением/строкой «Есть вложения».
    await create_attachment(
        ticket_id=snapshot_ticket,
        file_id="selftest-photo-file-id",
        file_type="photo",
        uploaded_by=1001,
    )
    incoming_card_bot = _FakeBot()
    await notify_department_about_ticket(
        bot=incoming_card_bot,
        department="purchasing",
        text="Этот старый текст уведомления не должен использоваться",
        exclude_telegram_id=1001,
        ticket_id=snapshot_ticket,
        use_ticket_actions=True,
        render_ticket_card=True,
    )
    _assert(len(incoming_card_bot.media) == 2, "Новая входящая карточка с фото не доставлена обоим закупщикам")
    _assert(
        all(item[0] == "photo" and item[2] == "selftest-photo-file-id" for item in incoming_card_bot.media),
        "Вложение нового входящего тикета не стало частью карточки",
    )
    _assert(
        all(f"🎫 Тикет #{snapshot_ticket}" in item[3] and "Когда будет поставка?" in item[3] for item in incoming_card_bot.media),
        "Первое уведомление не использует актуальный формат карточки тикета",
    )

    fake_bot = _FakeBot()
    await send_live_ticket_text(
        fake_bot,
        chat_id=1001,
        ticket_id=snapshot_ticket,
        text=f"Обновлённая карточка тикета #{snapshot_ticket}",
    )
    _assert(fake_bot.deleted == [(1001, 101), (1001, 102)], "Старая карточка тикета не удалена")
    _assert(
        await get_ticket_message_ids(snapshot_ticket, 1001) == [501],
        "Новая живая карточка тикета не стала актуальной",
    )

    await set_ui_message_ids(1001, "primary", [201, 202])
    ui_bot = _FakeBot()
    await send_ui_text(
        ui_bot,
        chat_id=1001,
        text="Новый служебный экран",
    )
    _assert(
        ui_bot.deleted == [(1001, 201), (1001, 202)],
        "Предыдущий служебный экран не удаляется",
    )
    _assert(
        await get_ui_message_ids(1001, "primary") == [501],
        "Новый служебный экран не стал единственным актуальным",
    )

    await set_ui_message_ids(1001, "primary", [777])
    edit_bot = _FakeBot()
    await send_ui_text(edit_bot, chat_id=1001, text="Трансформированный экран")
    _assert(
        getattr(edit_bot, "edited", []) == [(1001, 777, "Трансформированный экран")],
        "Одиночный inline-экран не редактируется на месте",
    )
    _assert(not edit_bot.sent, "При успешном editMessageText создано лишнее сообщение")

    reply_menu_texts = {button.text for row in bottom_menu_for_role("client").keyboard for button in row}
    inline_callbacks = {
        button.callback_data
        for row in main_menu_for_role("purchasing").inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert("🔎 Узнать статус заказа" in reply_menu_texts, "В нижнем меню нет проверки заказа")
    _assert("🏠 Меню" in reply_menu_texts, "В нижнем меню нет постоянной кнопки вызова inline-панели")
    _assert("📂 Работа с тикетами" in reply_menu_texts, "В PC-first нижнем меню нет прямого входа в работу с тикетами")
    _assert("📤 Исходящие" not in reply_menu_texts, "Исходящие не спрятаны из главного меню")
    _assert("❓ Помощь" in reply_menu_texts, "В нижнем меню нет центра помощи")
    _assert("order_status_start" in inline_callbacks, "В inline-меню нет проверки заказа")
    _assert("ticket_work_menu" in inline_callbacks, "В inline-меню нет раздела работы с тикетами")
    reply_layout = [[button.text for button in row] for row in bottom_menu_for_role("client").keyboard]
    inline_layout = [[button.text for button in row] for row in main_menu_for_role("client").inline_keyboard]
    admin_inline_layout = [[button.text for button in row] for row in main_menu_for_role("admin", is_admin=True).inline_keyboard]
    expected_reply_layout = [
        ["➕ Создать тикет"],
        ["📂 Работа с тикетами", "🔎 Узнать статус заказа"],
        ["🏠 Меню", "❓ Помощь"],
    ]
    expected_inline_layout = [
        ["➕ Создать тикет"],
        ["🔎 Узнать статус заказа"],
        ["📂 Работа с тикетами", "❓ Помощь"],
    ]
    _assert(reply_layout == expected_reply_layout, "Нижнее PC-first меню изменилось, хотя должно остаться прежним")
    _assert(inline_layout == expected_inline_layout, "Inline PC-first меню имеет неверное расположение кнопок")
    _assert(
        all("🏠 Меню" not in row for row in inline_layout),
        "В главной inline-панели осталась бессмысленная кнопка «Меню», ведущая сама на себя",
    )
    _assert(
        admin_inline_layout == expected_inline_layout + [["⚙️ Админка"]],
        "Админская inline-панель должна отличаться только четвёртым рядом «Админка»",
    )

    dashboard_text = await build_main_menu_text(1001, "client")
    _assert("📊 <b>Рабочая сводка</b>" in dashboard_text, "Главное меню не стало информационной панелью")
    _assert("Клиентский отдел" in dashboard_text and "Закупка" in dashboard_text, "В сводке не показаны оба отдела")
    _assert("не обработано" in dashboard_text, "В сводке нет счётчика необработанных тикетов")
    _assert("Просрочено (не сегодня)" in dashboard_text, "В сводке нет тикетов старше текущего календарного дня")

    work_callbacks = {
        button.callback_data
        for row in ticket_work_menu_keyboard().inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(
        {"outgoing_tickets", "incoming_tickets", "work_tickets", "archive_tickets", "work_hub"} <= work_callbacks,
        "Подменю работы с тикетами неполное",
    )
    admin_callbacks = {
        button.callback_data
        for row in admin_menu().inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(
        {"admin_section_users", "admin_section_tickets", "admin_section_stats", "admin_section_system"} <= admin_callbacks,
        "Админка не сгруппирована в четыре PC-first раздела",
    )
    _assert("admin_templates" not in admin_callbacks, "Отключённые шаблоны остались в админке")

    workspace_list = ticket_workspace_keyboard(
        [await get_ticket_by_id(assignment_ticket)],
        list_type="incoming",
        page=0,
        page_size=5,
        total=1,
    )
    workspace_callbacks = {
        button.callback_data
        for row in workspace_list.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(
        {"workspace_list:incoming:0", "workspace_list:work:0", "workspace_list:outgoing:0", "workspace_list:archive:0"} <= workspace_callbacks,
        "Workspace не содержит переключатели списков",
    )
    _assert("workspace_review_start:incoming" in workspace_callbacks, "В workspace нет режима разбора")

    workspace_card = workspace_ticket_action_keyboard(
        await get_ticket_by_id(assignment_ticket),
        {"telegram_id": requester, "role": "purchasing"},
        False,
        position=0,
        total=2,
    )
    workspace_card_texts = {button.text for row in workspace_card.inline_keyboard for button in row}
    _assert("💬 Ответить" in workspace_card_texts, "В workspace пропала отдельная кнопка «Ответить»")
    _assert("🏁 Выполнить" in workspace_card_texts, "В workspace пропала отдельная кнопка «Выполнить»")
    _assert("✅ Ответить и выполнить" not in workspace_card_texts, "В workspace осталась удалённая кнопка «Ответить и выполнить»")
    workspace_rows = [[button.text for button in row] for row in workspace_card.inline_keyboard]
    flattened_workspace = [text for row in workspace_rows for text in row]
    _assert("След. тикет ▶" in flattened_workspace, "Кнопка следующего тикета имеет неясную подпись")
    _assert("⚙️ Все возможные действия" in flattened_workspace, "Расширенные действия не получили понятную подпись")
    _assert(workspace_rows[-1] == ["↩️ К списку"], "Кнопка возврата к списку должна быть последней в карточке")
    legacy_card = ticket_action_keyboard(
        await get_ticket_by_id(assignment_ticket),
        {"telegram_id": requester, "role": "purchasing"},
        False,
    )
    legacy_card_texts = {button.text for row in legacy_card.inline_keyboard for button in row}
    _assert("💬 Ответить" in legacy_card_texts, "В legacy-карточке пропала отдельная кнопка «Ответить»")
    _assert("🏁 Выполнить" in legacy_card_texts, "В legacy-карточке пропала отдельная кнопка «Выполнить»")
    _assert("✅ Ответить и выполнить" not in legacy_card_texts, "В уведомлениях осталась удалённая кнопка «Ответить и выполнить»")
    _assert(html_escape("5 < 10 & 12 > 3") == "5 &lt; 10 &amp; 12 &gt; 3", "HTML не экранируется")
    _assert(format_moscow_datetime("2026-01-01 00:00:00").endswith("03:00 МСК"), "Неверное преобразование UTC в МСК")

    stats = await collect_daily_stats()
    _assert(int(stats["total_open"] or 0) >= 1, "Ежедневная статистика не собрана")
    csv_payload = await export_statistics_csv()
    _assert(csv_payload.startswith(b"\xef\xbb\xbf") and len(csv_payload) > 100, "CSV статистики не сформирован")

    # Telegram ограничивает callback_data 64 байтами: проверяем новые клавиатуры.
    keyboards = [
        work_hub_keyboard(12),
        legacy_card,
        workspace_list,
        workspace_card,
    ]
    for keyboard in keyboards:
        for row in keyboard.inline_keyboard:
            for button in row:
                callback = getattr(button, "callback_data", None)
                if callback:
                    _assert(len(callback.encode("utf-8")) <= 64, f"Слишком длинный callback_data: {callback}")

    export_path, export_count, export_digest = create_project_export()
    try:
        _assert(export_count > 20 and len(export_digest) == 64, "Архив исходного кода не сформирован")
        with zipfile.ZipFile(export_path) as source_archive:
            export_names = set(source_archive.namelist())
        _assert("app/database.py" in export_names and "main.py" in export_names, "В выгрузке нет исходного кода")
        _assert("app/services/project_export.py" in export_names, "В выгрузке нет сервиса экспорта")
        _assert("bot.db" not in export_names and not any(name.startswith("backups/") for name in export_names), "В выгрузку попали рабочие данные")
    finally:
        export_path.unlink(missing_ok=True)

    backup_path = await create_database_backup(keep_last=2)
    _assert(backup_path.is_file(), "Резервная копия не создана")
    with sqlite3.connect(backup_path) as backup:
        result = backup.execute("PRAGMA integrity_check").fetchone()
        _assert(result and str(result[0]).lower() == "ok", "Резервная копия повреждена")

    with sqlite3.connect(database_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        _assert(integrity and str(integrity[0]).lower() == "ok", "Тестовая база повреждена")


def main() -> int:
    asyncio.run(run_selftest())
    print("SELFTEST_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
