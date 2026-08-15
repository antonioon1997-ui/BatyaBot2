from __future__ import annotations

import asyncio
import inspect
import os
import re
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
                (1002, "client_two", "Клиент Два", "client"),
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
        notify_opposite_department_about_ticket,
        notify_opposite_department_events_about_ticket,
    )
    from app.handlers.tickets.actions import _submit_comment_text
    from app.handlers.tickets.workspace import _current_order_block, _load_tickets, build_workspace_ticket_text, build_workspace_ticket_view
    from app.keyboards.admin import admin_menu
    from app.keyboards.common import bottom_menu_for_role, main_menu_for_role, ticket_work_menu_keyboard
    from app.keyboards.productivity import work_hub_keyboard
    from app.keyboards.tickets import (
        comment_input_keyboard,
        notification_input_cancel_keyboard,
        notification_center_keyboard,
        post_create_options_keyboard,
        ticket_action_keyboard,
        ticket_notification_keyboard,
        ticket_activity_keyboard,
        ticket_workspace_keyboard,
        workspace_ticket_action_keyboard,
    )
    from app.services.analytics import collect_daily_stats, export_statistics_csv
    from app.services.attachments import create_attachment, get_ticket_attachments
    from app.services.backups import create_database_backup
    from app.services.feedback import create_feedback, get_feedback, list_feedback
    from app.services.polls import create_poll, get_poll_results, upsert_vote
    from app.services.preferences import get_message_style, set_message_style
    from app.services.project_export import create_project_export
    from app.services.ticket_messages import get_ticket_message_ids, send_live_ticket_text, set_ticket_message_ids
    from app.services.ticket_activity import (
        TICKET_ACTIVITY_SLOT,
        acknowledge_ticket_activity,
        get_activity_ticket_ids,
        get_notification_center_stats,
        get_ticket_activity_events,
        show_ticket_activity_panel,
    )
    from app.services.ui_context import get_ui_context, set_ticket_list_context
    from app.services.ui_messages import (
        get_ui_message_ids, is_ui_slot_message, reanchor_ui_text_slot, send_ui_text, set_ui_message_ids
    )
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
        add_ticket_event,
        close_due_auto_close_ticket,
        count_tickets_for_department_reminder,
        create_ticket,
        get_active_users_by_department,
        get_archive_incoming_tickets,
        get_ticket_by_id,
        get_ticket_comments,
        get_ticket_events,
        search_archive_tickets,
        schedule_ticket_auto_close,
        set_ticket_category,
        set_ticket_priority,
        take_ticket,
        update_ticket_status,
    )
    from app.services.update_manager import predict_next_version
    from app.services.update_notices import (
        UPDATE_NOTICE_SLOT,
        build_update_notice_text,
        update_notice_keyboard,
    )
    from app.services.system_notices import (
        MAINTENANCE_TTL_MINUTES,
        system_notice_slot,
    )
    from app.services.ticket_post_create import (
        mark_post_create_choice,
        register_post_create_context,
        verify_post_create_persisted,
    )
    from app.services.ticket_history_ui import get_ticket_history_blocks
    from app.services.ticket_media_ui import show_ticket_media_header
    from app.services.ticket_notifications import (
        get_pending_ticket_notification_ids,
        queue_and_deliver_ticket_notification,
        queue_and_deliver_ticket_notifications,
    )
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

    compact_notice = build_update_notice_text(
        "9.9.9", role="client", is_admin=False, expanded=False
    )
    _assert(
        "Что изменилось" in compact_notice and "24 часа" in compact_notice,
        "Компактное уведомление об обновлении не содержит раскрытие/TTL",
    )
    compact_notice_keyboard = update_notice_keyboard("9.9.9", expanded=False)
    compact_notice_callbacks = {
        button.callback_data
        for row in compact_notice_keyboard.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(
        "update_notice_expand:9.9.9" in compact_notice_callbacks,
        "У уведомления об обновлении нет кнопки раскрытия",
    )
    _assert(UPDATE_NOTICE_SLOT == "update_notice", "Изменён slot уведомления об обновлении")
    _assert(system_notice_slot("maintenance") == "system_notice:maintenance", "Сломан системный UI-slot")
    _assert(MAINTENANCE_TTL_MINUTES == 30, "Maintenance notice должен удаляться через 30 минут")

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
            "ticket_message_registry", "ui_message_registry", "ui_render_state", "ui_navigation_state",
            "ticket_notification_outbox", "ticket_activity_events", "update_notice_meta",
            "system_notice_meta", "ticket_post_create_ui",
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

    # 2.9.8: админ одновременно остаётся сотрудником своего отдела в обычном workspace.
    # Раньше is_admin=True обнулял department, поэтому «Входящие» показывали вообще все
    # активные тикеты и пересекались с «Исходящими».
    admin_workspace_incoming_ticket = await create_ticket(
        title="Входящий для закупщика-админа",
        description="Должен быть только во входящих закупки",
        order_number=None,
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
    )
    admin_as_purchaser = {"telegram_id": 2001, "role": "purchaser"}
    admin_incoming_rows = await _load_tickets(2001, admin_as_purchaser, True, "incoming")
    admin_incoming_ids = {int(row["id"]) for row in admin_incoming_rows}
    _assert(
        admin_workspace_incoming_ticket in admin_incoming_ids,
        "Админ-закупщик не видит настоящий входящий тикет своего отдела",
    )
    _assert(
        confirmation_ticket not in admin_incoming_ids,
        "Исходящий тикет админа-закупщика ошибочно попал во «Входящие»",
    )
    admin_filtered_incoming_rows = await _load_tickets(
        2001, admin_as_purchaser, True, "incoming", {"status": "new"}
    )
    admin_filtered_incoming_ids = {int(row["id"]) for row in admin_filtered_incoming_rows}
    _assert(
        confirmation_ticket not in admin_filtered_incoming_ids,
        "Фильтр «Входящие» у администратора снова потерял ограничение по отделу",
    )

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
        f"ticket_options_done:{confirmation_ticket}" in post_create_callbacks,
        "В post-create панели нет кнопки возврата в главное меню с текущими параметрами",
    )
    await register_post_create_context(
        ticket_id=confirmation_ticket,
        user_id=2001,
        source_message_ids=[777],
        expected_attachment_count=0,
    )
    _assert(
        await mark_post_create_choice(confirmation_ticket, 2001, field="priority") is False,
        "Post-create flow завершился после выбора только срочности",
    )
    _assert(
        await mark_post_create_choice(confirmation_ticket, 2001, field="category") is True,
        "Post-create flow не распознал выбор обоих параметров",
    )
    persisted_ok, persisted_error = await verify_post_create_persisted(confirmation_ticket, 2001)
    _assert(persisted_ok, f"Post-create проверка сохранности тикета не прошла: {persisted_error}")

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
        # Регрессия 2.9.1: у заказа может не быть active-позиции МойСклад,
        # но оставаться актуальное active=1 назначение поставщика. Клиентский
        # отдел в таком случае всё равно должен видеть SKU/количество/статус.
        order_db.execute(
            "INSERT INTO moysklad_orders VALUES ('ms2', '11738', '[O] Ожидание товара', 1, '')"
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
            "INSERT INTO current_supplier_statuses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                # В live-data остаток 1, но заказу BS назначено 2: основная строка должна показать 2.
                ('privoz', '388776', '14163', '14163', '2', '2', '1', 'Выдан частично', 'ready', '11786'),
                ('pozakupy', '119861/245415', '28599', '28599', '2', '2', '2', 'На складе', 'ready', '11786'),
                ('privoz', '399999', '288', '288', '1', '1', '1', 'Выдан со склада', 'issued', '11786'),
                # Старая связь остаётся видимой во view, но active=0 в supplier_order_state — не показывать.
                ('privoz', '399998', '288', '288', '9', '9', '9', 'Выдан со склада', 'issued', '11786'),
                # У 11738 намеренно нет строки в moysklad_order_items.
                ('pozakupy', '116259/238366', '10948', '10948', '1', '1', '1', 'На складе', 'ready', '11738'),
            ],
        )
        order_db.executemany(
            "INSERT INTO supplier_status_current VALUES (?, ?, ?, ?, ?, ?)",
            [
                ('privoz', '388776', 'Выдан частично', 'ready', '2026-08-08T14:45:00+00:00', None),
                ('pozakupy', '119861/245415', 'На складе', 'ready', '2026-08-08T17:45:00+03:00', None),
                ('privoz', '399999', 'Выдан со склада', 'issued', '2026-08-08T12:00:00+00:00', '2026-08-09T10:00:00+00:00'),
                ('privoz', '399998', 'Выдан со склада', 'issued', '2026-08-07T12:00:00+00:00', '2026-08-08T10:00:00+00:00'),
                ('pozakupy', '116259/238366', 'На складе', 'ready', '2026-08-06T11:59:00+00:00', None),
            ],
        )
        order_db.executemany(
            "INSERT INTO supplier_orders(id, provider, external_order_id) VALUES (?, ?, ?)",
            [
                (1, 'privoz', '388776'),
                (2, 'pozakupy', '119861/245415'),
                (3, 'privoz', '399999'),
                (4, 'privoz', '399998'),
                (5, 'pozakupy', '116259/238366'),
            ],
        )
        order_db.executemany(
            "INSERT INTO supplier_order_state(supplier_order_id, bs_order_number, quantity_text, active) VALUES (?, ?, ?, ?)",
            [
                (1, '11786', '2', 1),
                (2, '11786', '2', 1),
                (3, '11786', '1', 1),
                (4, '11786', '9', 0),
                (5, '11738', '1', 1),
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

    supplier_only_lookup = await get_order_status("11738")
    _assert(supplier_only_lookup.record is not None, "OrderExporter не вернул BS без active MS-позиций")
    _assert(
        supplier_only_lookup.record.client_items == (
            "10948, 1 шт, На складе — Приход на склад 06.08.2026 14:59",
        ),
        "Клиентский статус потерял active supplier assignment при отсутствии active MS-позиции",
    )
    _assert(
        all("116259/238366" not in line for line in supplier_only_lookup.record.client_items)
        and any("заказ 116259/238366" in line for line in supplier_only_lookup.record.purchasing_items),
        "Клиентское представление раскрыло номер заказа поставщика или закупочное представление его потеряло",
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

    workspace_builder_source = inspect.getsource(build_workspace_ticket_view)
    _assert(
        "Последний комментарий" not in workspace_builder_source
        and "get_ticket_history_blocks" in workspace_builder_source
        and "compose_inline_ticket_history" in workspace_builder_source,
        "Workspace должен показывать единую историю комментариев и изменений с безопасным fallback",
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

    # UX-history: внутренние методы не должны становиться пользовательскими
    # действиями. order_status_attached существует в ticket_events, но отсутствует
    # в визуальной истории.
    await add_ticket_event(
        snapshot_ticket,
        "order_status_attached",
        actor_telegram_id=1001,
        details="К тикету приложен снимок статуса заказа из OrderExporter",
    )
    visible_history = await get_ticket_history_blocks(snapshot_ticket)
    _assert(
        all("order_status_attached" not in block.plain and "снимок статуса заказа" not in block.plain for block in visible_history),
        "Внутренний order_status_attached просочился в пользовательскую историю",
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
            self.media_groups: list[list[str]] = []
            self.deleted: list[tuple[int, int]] = []
            self.next_message_id = 500

        async def send_message(self, *, chat_id: int, text: str, reply_markup=None, **kwargs):
            self.sent.append((int(chat_id), str(text)))
            self.next_message_id += 1
            return _FakeSentMessage(self.next_message_id)

        async def _send_media(self, kind: str, chat_id: int, file_id: str, caption: str = "", reply_markup=None):
            self.media.append((kind, int(chat_id), str(file_id), str(caption or "")))
            self.next_message_id += 1
            return _FakeSentMessage(self.next_message_id)

        async def send_photo(self, *, chat_id: int, photo: str, caption: str = "", reply_markup=None, **kwargs):
            return await self._send_media("photo", chat_id, photo, caption, reply_markup)

        async def send_document(self, *, chat_id: int, document: str, caption: str = "", reply_markup=None, **kwargs):
            return await self._send_media("document", chat_id, document, caption, reply_markup)

        async def send_video(self, *, chat_id: int, video: str, caption: str = "", reply_markup=None, **kwargs):
            return await self._send_media("video", chat_id, video, caption, reply_markup)

        async def send_media_group(self, *, chat_id: int, media, **kwargs):
            file_ids = [str(getattr(item, "media", "")) for item in media]
            self.media_groups.append(file_ids)
            messages = []
            for file_id in file_ids:
                self.media.append(("photo", int(chat_id), file_id, ""))
                self.next_message_id += 1
                messages.append(_FakeSentMessage(self.next_message_id))
            return messages

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
    # Дальнейшие legacy activity-проверки ожидают текстовую панель; отдельный
    # media-center regression test находится ниже.
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM ticket_attachments WHERE ticket_id = ?", (snapshot_ticket,))
        connection.commit()

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

    # 2.9.4: Центр уведомлений показывает не только новые события, но и
    # всю сохранённую историю комментариев, чтобы ответ всегда имел контекст.
    await add_ticket_comment(snapshot_ticket, 2001, "Контекст из предыдущего комментария", start_work_if_new=False)

    # События по тикетам теперь агрегируются во второй опциональной панели.
    # Она имеет отдельный UI-slot, не трогает live-card и при новом событии
    # поднимается свежим сообщением (чтобы Telegram прислал уведомление).
    await set_ticket_message_ids(snapshot_ticket, 1001, [901])
    event_bot = _FakeBot()
    delivered = await queue_and_deliver_ticket_notification(
        event_bot,
        ticket_id=snapshot_ticket,
        recipient_id=1001,
        notification_type="ticket_comment",
        text=f"💬 Новый комментарий в тикете #{snapshot_ticket}\n\nТестовый ответ",
        keyboard_mode="notification",
    )
    _assert(delivered, "Event-уведомление не доставлено в activity-панель")
    _assert(len(event_bot.sent) == 1, "Activity-панель не создана свежим сообщением")
    _assert(
        await get_ui_message_ids(1001, TICKET_ACTIVITY_SLOT) == [501],
        "Activity-панель не зарегистрирована в отдельном UI-slot",
    )
    _assert(
        await get_ticket_message_ids(snapshot_ticket, 1001) == [901],
        "Activity-панель изменила ticket_message_registry",
    )
    _assert(await get_activity_ticket_ids(1001) == [snapshot_ticket], "Тикет не попал в очередь новых действий")

    _assert(
        "История тикета" in event_bot.sent[-1][1]
        and "Контекст из предыдущего комментария" in event_bot.sent[-1][1],
        "Центр уведомлений не показывает полную историю тикета",
    )

    delivered = await queue_and_deliver_ticket_notification(
        event_bot,
        ticket_id=snapshot_ticket,
        recipient_id=1001,
        notification_type="ticket_completed",
        text=f"✅ Тикет #{snapshot_ticket}: выполнен и закрыт",
        keyboard_mode="actions",
    )
    _assert(delivered, "Отметка о выполнении не добавлена в activity-панель")
    _assert(len(event_bot.sent) == 2, "Новое событие не подняло activity-панель свежим сообщением")
    _assert((1001, 501) in event_bot.deleted, "Предыдущая activity-панель не заменена новой")
    activity_events = await get_ticket_activity_events(1001, snapshot_ticket)
    _assert(len(activity_events) == 2, "Комментарий и выполнение не накопились внутри одного тикета")
    _assert(not await get_pending_ticket_notification_ids(), "Доставленное уведомление осталось pending")

    activity_keyboard = ticket_activity_keyboard(snapshot_ticket, position=0, total=2)
    activity_callbacks = {
        button.callback_data
        for row in activity_keyboard.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(f"ticket_activity_open:{snapshot_ticket}" in activity_callbacks, "В activity-панели нет открытия тикета")
    _assert(f"ticket_activity_ack:{snapshot_ticket}" in activity_callbacks, "В activity-панели нет acknowledge")

    await acknowledge_ticket_activity(1001, snapshot_ticket)
    _assert(not await get_activity_ticket_ids(1001), "Ознакомленный тикет не убран из activity-очереди")
    await show_ticket_activity_panel(event_bot, recipient_id=1001, fresh=False)
    _assert(await get_ui_message_ids(1001, TICKET_ACTIVITY_SLOT) == [], "Пустая activity-панель не исчезла")

    # 2.9.5: однотипное фото должно быть частью самого Центра уведомлений, а
    # не превращаться обратно в строку «Вложения: 1».
    media_center_ticket = await create_ticket(
        title="Тикет с фото для Центра",
        description="Проверьте изображение",
        order_number=None,
        created_by=1001,
        requester_department="client",
        executor_department="purchasing",
    )
    await create_attachment(
        ticket_id=media_center_ticket,
        file_id="center-photo-file-id",
        file_type="photo",
        uploaded_by=1001,
    )
    media_center_bot = _FakeBot()
    _assert(await queue_and_deliver_ticket_notification(
        media_center_bot,
        ticket_id=media_center_ticket,
        recipient_id=2001,
        notification_type="ticket_new",
        text=f"🆕 Новый тикет #{media_center_ticket}",
    ), "Тикет с фото не доставлен в Центр уведомлений")
    _assert(
        any(item[0] == "photo" and item[2] == "center-photo-file-id" for item in media_center_bot.media),
        "Центр уведомлений не встроил однотипное фото в свою выдачу",
    )
    await acknowledge_ticket_activity(2001, media_center_ticket)

    # Ответ/дополнение может состоять только из вложения. Оно сохраняется в той
    # же ticket_attachments и затем входит в общий media-header тикета.
    comments_before = len(await get_ticket_comments(media_center_ticket, limit=None))
    await add_ticket_comment(
        media_center_ticket,
        2001,
        None,
        attachment={
            "file_id": "reply-photo-file-id",
            "file_unique_id": "reply-photo-unique-id",
            "file_type": "photo",
            "file_name": None,
            "caption": None,
        },
        start_work_if_new=True,
    )
    _assert(
        len(await get_ticket_comments(media_center_ticket, limit=None)) == comments_before,
        "Вложение без текста создало пустой комментарий",
    )
    header_bot = _FakeBot()
    header_ids = await show_ticket_media_header(
        header_bot,
        chat_id=2001,
        ticket_id=media_center_ticket,
        attachments=await get_ticket_attachments(media_center_ticket),
    )
    _assert(len(header_ids) == 2, "Media-header не показывает исходное и добавленное вложение вместе")
    _assert(
        {item[2] for item in header_bot.media} == {"center-photo-file-id", "reply-photo-file-id"},
        "Дополнительное вложение не попало в общий header тикета",
    )
    _assert(
        header_bot.media_groups == [["center-photo-file-id", "reply-photo-file-id"]],
        "Два изображения header не сгруппированы в один нативный Telegram-альбом",
    )

    # Telegram допускает максимум 10 элементов в media group. 12 изображений
    # должны отрисоваться двумя альбомами 10+2, без потери message_id.
    album_bot = _FakeBot()
    album_rows = [
        {"file_id": f"album-photo-{index}", "file_type": "photo"}
        for index in range(12)
    ]
    album_ids = await show_ticket_media_header(
        album_bot,
        chat_id=2001,
        ticket_id=media_center_ticket,
        attachments=album_rows,
        slot="selftest_album_header",
    )
    _assert(len(album_ids) == 12, "Альбом потерял часть изображений")
    _assert(
        [len(group) for group in album_bot.media_groups] == [10, 2],
        "Изображения не разбиваются по лимиту Telegram 10 элементов на альбом",
    )

    # 2.9.3: «Ответить и выполнить» должно сохранять ДВА события, но поднимать
    # Центр уведомлений одним итоговым сообщением. Иначе комментарий снова
    # потеряется за отметкой выполнения либо появятся два лишних push-сообщения.
    submit_comment_source = inspect.getsource(_submit_comment_text)
    _assert(
        "notify_opposite_department_events_about_ticket" in submit_comment_source
        and '"ticket_comment"' in submit_comment_source
        and '"ticket_completed"' in submit_comment_source,
        "Flow «Ответить и выполнить» снова не формирует два события для Центра уведомлений",
    )
    combined_bot = _FakeBot()
    combined_delivered = await queue_and_deliver_ticket_notifications(
        combined_bot,
        ticket_id=snapshot_ticket,
        recipient_id=1001,
        events=[
            (
                "ticket_comment",
                f"💬 Новый комментарий в тикете #{snapshot_ticket}\n\nКомбинированный ответ",
            ),
            (
                "ticket_completed",
                f"✅ Тикет #{snapshot_ticket}: выполнен и закрыт",
            ),
        ],
    )
    _assert(combined_delivered, "Связанные комментарий+выполнение не доставлены")
    combined_events = await get_ticket_activity_events(1001, snapshot_ticket)
    _assert(
        [str(row["notification_type"]) for row in combined_events] == ["ticket_comment", "ticket_completed"],
        "«Ответить и выполнить» не сохранило комментарий и выполнение раздельными событиями",
    )
    _assert(len(combined_bot.sent) == 1, "Связанные события подняли Центр более одного раза")
    combined_panel_text = combined_bot.sent[-1][1]
    _assert(
        "Комбинированный ответ" in combined_panel_text and "✅" in combined_panel_text,
        "Итоговая activity-панель не показывает комментарий вместе с выполнением",
    )
    await acknowledge_ticket_activity(1001, snapshot_ticket)

    # 2.8.0: новый тикет и его последующие события до acknowledge остаются
    # одной карточкой во вкладке «Новые». После acknowledge следующее событие
    # этого же тикета уже должно попасть во вкладку «Обновления».
    center_bot = _FakeBot()
    _assert(await queue_and_deliver_ticket_notification(
        center_bot,
        ticket_id=snapshot_ticket,
        recipient_id=1001,
        notification_type="ticket_new",
        text=f"🆕 Новый тикет #{snapshot_ticket}",
    ), "Новый тикет не доставлен в центр уведомлений")
    _assert(await queue_and_deliver_ticket_notification(
        center_bot,
        ticket_id=snapshot_ticket,
        recipient_id=1001,
        notification_type="ticket_comment",
        text=f"💬 Новый комментарий в тикете #{snapshot_ticket}\n\nПока ещё новый",
    ), "Комментарий к новому тикету не доставлен")
    center_stats = await get_notification_center_stats(1001)
    _assert(center_stats.new_count == 1, "Новый тикет не попал во вкладку Новые")
    _assert(center_stats.update_ticket_count == 0, "Неознакомленный новый тикет задублирован в Обновления")
    _assert(len(await get_ticket_activity_events(1001, snapshot_ticket)) == 2, "События нового тикета не сгруппированы")

    snapshot_row = await get_ticket_by_id(snapshot_ticket)
    client_center_user = {"telegram_id": 1001, "role": "client"}
    center_keyboard = notification_center_keyboard(
        snapshot_row,
        client_center_user,
        False,
        ticket_id=snapshot_ticket,
        tab="new",
        position=0,
        total=1,
        new_count=1,
        update_ticket_count=0,
        update_event_count=0,
    )
    center_callbacks = {
        button.callback_data
        for row in center_keyboard.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert("notification_tab:new" in center_callbacks, "В центре уведомлений нет вкладки Новые")
    _assert("notification_tab:updates" in center_callbacks, "В центре уведомлений нет вкладки Обновления")
    _assert(f"notification_ack:new:{snapshot_ticket}" in center_callbacks, "Нет acknowledge нового тикета")

    # 2.9.0: Центр уведомлений — полноценная вторая рабочая поверхность.
    # Исполнитель должен иметь главные действия прямо в нём, без перехода в PRIMARY UI.
    purchasing_center_keyboard = notification_center_keyboard(
        snapshot_row,
        {"telegram_id": 2001, "role": "purchaser"},
        False,
        ticket_id=snapshot_ticket,
        tab="new",
        position=0,
        total=1,
        new_count=1,
        update_ticket_count=0,
        update_event_count=0,
    )
    purchasing_center_callbacks = {
        button.callback_data
        for row in purchasing_center_keyboard.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(f"ticket_comment:{snapshot_ticket}" in purchasing_center_callbacks, "В Центре уведомлений нет прямого Ответить")
    _assert(f"ticket_comment_done:{snapshot_ticket}" in purchasing_center_callbacks, "В Центре уведомлений нет «Ответить и выполнить»")
    _assert(f"ticket_resolve:{snapshot_ticket}" not in purchasing_center_callbacks, "В Центре уведомлений осталась отдельная кнопка Выполнить")
    _assert(not any(str(value).startswith("notification_open:") for value in purchasing_center_callbacks), "Центр уведомлений снова требует открытия PRIMARY тикета")

    optional_reply_keyboard = comment_input_keyboard(snapshot_ticket, allow_empty_completion=True)
    optional_reply_callbacks = {
        button.callback_data
        for row in optional_reply_keyboard.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(
        f"ticket_comment_done_empty:{snapshot_ticket}" in optional_reply_callbacks,
        "«Ответить и выполнить» не позволяет выполнить тикет без комментария",
    )
    # 2.9.4 regression: закрытие как неактуального из Центра уведомлений
    # использует compatibility-клавиатуру отмены. Символ обязан быть импортирован
    # в actions.py и сама клавиатура должна оставаться рабочей.
    from app.handlers.tickets import actions as ticket_actions_module
    _assert(
        getattr(ticket_actions_module, "notification_input_cancel_keyboard", None) is notification_input_cancel_keyboard,
        "ticket_cancel из Центра уведомлений потерял импорт notification_input_cancel_keyboard",
    )
    cancel_input_keyboard = notification_input_cancel_keyboard(snapshot_ticket)
    cancel_input_callbacks = {
        button.callback_data
        for row in cancel_input_keyboard.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(
        f"ticket_comment_input_cancel:{snapshot_ticket}" in cancel_input_callbacks,
        "Клавиатура отмены ввода для notification-flow не содержит callback отмены",
    )

    required_reply_keyboard = comment_input_keyboard(snapshot_ticket, allow_empty_completion=False)
    required_reply_callbacks = {
        button.callback_data
        for row in required_reply_keyboard.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert(
        f"ticket_comment_done_empty:{snapshot_ticket}" not in required_reply_callbacks,
        "Обычный «Ответить» ошибочно допускает пустой ответ",
    )

    await acknowledge_ticket_activity(1001, snapshot_ticket)
    _assert(await queue_and_deliver_ticket_notification(
        center_bot,
        ticket_id=snapshot_ticket,
        recipient_id=1001,
        notification_type="ticket_comment",
        text=f"💬 Новый комментарий в тикете #{snapshot_ticket}\n\nПосле ознакомления",
    ), "Обновление после acknowledge не доставлено")
    center_stats = await get_notification_center_stats(1001)
    _assert(center_stats.new_count == 0, "Ознакомленный тикет ошибочно остался в Новых")
    _assert(center_stats.update_ticket_count == 1 and center_stats.update_event_count == 1, "Счётчики Обновлений неверны")
    await acknowledge_ticket_activity(1001, snapshot_ticket)

    # 2.9.2: ответ закупки по тикету, созданному клиентским менеджером,
    # получает только автор тикета. Второй менеджер отдела не должен видеть
    # чужой ответ/закрытие в своём Центре уведомлений.
    department_event_bot = _FakeBot()
    snapshot_row = await get_ticket_by_id(snapshot_ticket)
    await notify_opposite_department_about_ticket(
        department_event_bot,
        ticket=snapshot_row,
        actor_department="purchasing",
        text=f"💬 Новый комментарий в тикете #{snapshot_ticket}\n\nТест автора",
        notification_type="ticket_comment",
        exclude_telegram_id=2001,
    )
    notified_chats = {chat_id for chat_id, _ in department_event_bot.sent}
    _assert(1001 in notified_chats, "Автор клиентского тикета не получил ответ закупки")
    _assert(1002 not in notified_chats, "Ответ закупки ошибочно разослан всему клиентскому отделу")
    _assert(await get_activity_ticket_ids(1002) == [], "Чужой клиентский тикет попал в Центр уведомлений второго менеджера")
    await acknowledge_ticket_activity(1001, snapshot_ticket)

    batch_route_bot = _FakeBot()
    await notify_opposite_department_events_about_ticket(
        batch_route_bot,
        ticket=snapshot_row,
        actor_department="purchasing",
        events=[
            ("ticket_comment", f"💬 Новый комментарий в тикете #{snapshot_ticket}\n\nОтвет + выполнение"),
            ("ticket_completed", f"✅ Тикет #{snapshot_ticket}: выполнен и закрыт"),
        ],
        exclude_telegram_id=2001,
    )
    batch_route_chats = {chat_id for chat_id, _ in batch_route_bot.sent}
    _assert(batch_route_chats == {1001}, "Пачка ответ+выполнение ушла не только автору клиентского тикета")
    _assert(len(await get_ticket_activity_events(1001, snapshot_ticket)) == 2, "Автор не получил оба связанных события")
    _assert(await get_ticket_activity_events(1002, snapshot_ticket) == [], "Второй менеджер получил чужую пачку событий")
    await acknowledge_ticket_activity(1001, snapshot_ticket)

    # Обратное направление остаётся отделовым: новое событие от автора-клиента
    # должно попасть всем активным исполнителям закупки.
    reverse_department_bot = _FakeBot()
    await notify_opposite_department_about_ticket(
        reverse_department_bot,
        ticket=snapshot_row,
        actor_department="client",
        text=f"💬 Дополнение автора в тикете #{snapshot_ticket}",
        notification_type="ticket_comment",
        exclude_telegram_id=1001,
    )
    reverse_chats = {chat_id for chat_id, _ in reverse_department_bot.sent}
    _assert(2001 in reverse_chats and 2002 in reverse_chats, "Дополнение клиента не доставлено всему отделу закупки")
    await acknowledge_ticket_activity(2001, snapshot_ticket)
    await acknowledge_ticket_activity(2002, snapshot_ticket)

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
    _assert(await is_ui_slot_message(1001, "primary", 777), "Generic UI-slot detection не узнаёт PRIMARY сообщение")
    await set_ui_message_ids(1001, TICKET_ACTIVITY_SLOT, [778])
    _assert(await is_ui_slot_message(1001, TICKET_ACTIVITY_SLOT, 778), "Generic UI-slot detection не узнаёт Центр уведомлений")
    await set_ui_message_ids(1001, TICKET_ACTIVITY_SLOT, [])
    edit_bot = _FakeBot()
    await send_ui_text(edit_bot, chat_id=1001, text="Трансформированный экран")
    _assert(
        getattr(edit_bot, "edited", []) == [(1001, 777, "Трансформированный экран")],
        "Одиночный inline-экран не редактируется на месте",
    )
    _assert(not edit_bot.sent, "При успешном editMessageText создано лишнее сообщение")

    # Системное уведомление физически нельзя вставить выше уже существующего
    # сообщения Telegram, поэтому footer поднимается тихим пересозданием текущего
    # render-state. Проверяем, что reanchor сохраняет экран и заменяет старый ID.
    reanchor_bot = _FakeBot()
    await reanchor_ui_text_slot(reanchor_bot, chat_id=1001, slot="primary")
    _assert(reanchor_bot.sent and reanchor_bot.sent[-1][1] == "Трансформированный экран", "Reanchor потерял текущий PRIMARY UI")
    _assert((1001, 777) in reanchor_bot.deleted, "Reanchor не удалил прежнюю PRIMARY панель")

    # 2.9.0: статический smoke-аудит админских кнопок. Любой callback_data,
    # генерируемый админскими клавиатурами и имеющий статический префикс, должен
    # встречаться в callback-handler'ах. Также main_menu не должен иметь два
    # конкурирующих exact-handler'а (это уже ломало единую inline-панель).
    project_root = Path(__file__).resolve().parents[1]
    keyboard_sources = "\n".join(
        (project_root / relative).read_text(encoding="utf-8")
        for relative in (
            "app/keyboards/admin.py",
            "app/keyboards/feedback.py",
            "app/keyboards/productivity.py",
            "app/keyboards/ui_metrics.py",
        )
    )
    handler_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (project_root / "app/handlers").rglob("*.py")
    )
    generated_admin_callbacks: set[tuple[str, str]] = set()
    for match in re.finditer(r"callback_data\s*=\s*f?[\"']([^\"']+)[\"']", keyboard_sources):
        raw = match.group(1)
        prefix = raw.split("{", 1)[0]
        if prefix:
            generated_admin_callbacks.add((raw, prefix))
    handler_exact = set(re.findall(r"F\.data\s*==\s*[\"']([^\"']+)[\"']", handler_sources))
    handler_prefixes = set(re.findall(r"F\.data\.startswith\([\"']([^\"']+)[\"']\)", handler_sources))

    def _admin_callback_is_wired(raw: str, prefix: str) -> bool:
        if "{" not in raw and raw in handler_exact:
            return True
        return any(raw.startswith(value) or prefix.startswith(value) for value in handler_prefixes)

    missing_admin_callbacks = sorted(
        raw for raw, prefix in generated_admin_callbacks
        if not _admin_callback_is_wired(raw, prefix)
    )
    _assert(not missing_admin_callbacks, f"Админские кнопки без handler-а: {missing_admin_callbacks}")
    main_menu_exact_handlers = len(re.findall(r"@router\.callback_query\(F\.data\s*==\s*[\"']main_menu[\"']\)", handler_sources))
    _assert(main_menu_exact_handlers == 1, f"Найдено конкурирующих main_menu handler-ов: {main_menu_exact_handlers}")

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
    _assert("✅ Ответить и выполнить" in workspace_card_texts, "В workspace нет «Ответить и выполнить»")
    _assert("🏁 Выполнить" not in workspace_card_texts, "В workspace осталась отдельная кнопка «Выполнить»")
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
    _assert("✅ Ответить и выполнить" in legacy_card_texts, "В legacy-карточке нет «Ответить и выполнить»")
    _assert("🏁 Выполнить" not in legacy_card_texts, "В legacy-карточке осталась отдельная кнопка «Выполнить»")
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
