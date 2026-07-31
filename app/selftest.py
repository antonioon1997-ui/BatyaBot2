from __future__ import annotations

import asyncio
import os
import sqlite3
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
    from app.handlers.tickets.utils import can_user_return_ticket, extract_order_number
    from app.keyboards.common import bottom_menu_for_role, main_menu_for_role
    from app.keyboards.productivity import work_hub_keyboard
    from app.keyboards.tickets import ticket_action_keyboard
    from app.services.analytics import collect_daily_stats, export_statistics_csv
    from app.services.backups import create_database_backup
    from app.services.feedback import create_feedback, get_feedback, list_feedback
    from app.services.polls import create_poll, get_poll_results, upsert_vote
    from app.services.preferences import get_message_style, set_message_style
    from app.services.ui_versions import (
        CURRENT_UI_ID,
        LEGACY_UI_ID,
        activate_ui_version,
        ensure_ui_versions,
        get_active_ui_id,
        help_button_enabled,
        list_ui_versions,
    )
    from app.services.templates import create_response_template, get_response_templates, update_response_template
    from app.services.order_status import (
        build_order_status_index,
        extract_order_number_from_query,
        format_purchasing_lines,
    )
    from app.services.tickets import (
        add_ticket_comment,
        close_due_auto_close_ticket,
        count_tickets_for_department_reminder,
        create_ticket,
        get_active_users_by_department,
        get_ticket_by_id,
        get_ticket_events,
        schedule_ticket_auto_close,
        set_ticket_category,
        set_ticket_priority,
        take_ticket,
        update_ticket_status,
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
    from app.handlers import admin, admin_feedback, admin_productivity, help, start, system, tickets, updater  # noqa: F401

    _prepare_legacy_database(database_path)
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
            "feedback_messages", "polls", "poll_votes", "admin_notes",
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
    _assert(help_button_enabled(), "В интерфейсе 2.3 скрыта кнопка помощи")
    _assert(len(list_ui_versions()) <= 5, "Хранится больше пяти версий интерфейса")
    activate_ui_version(LEGACY_UI_ID)
    _assert(not help_button_enabled(), "Классический интерфейс не скрывает центр помощи")
    activate_ui_version(CURRENT_UI_ID)

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

    reply_menu_texts = {button.text for row in bottom_menu_for_role("client").keyboard for button in row}
    inline_callbacks = {
        button.callback_data
        for row in main_menu_for_role("purchasing").inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    }
    _assert("🔎 Узнать статус заказа" in reply_menu_texts, "В нижнем меню нет проверки заказа")
    _assert("❓ Помощь" in reply_menu_texts, "В нижнем меню нет центра помощи")
    _assert("order_status_start" in inline_callbacks, "В inline-меню нет проверки заказа")
    _assert(html_escape("5 < 10 & 12 > 3") == "5 &lt; 10 &amp; 12 &gt; 3", "HTML не экранируется")
    _assert(format_moscow_datetime("2026-01-01 00:00:00").endswith("03:00 МСК"), "Неверное преобразование UTC в МСК")

    stats = await collect_daily_stats()
    _assert(int(stats["total_open"] or 0) >= 1, "Ежедневная статистика не собрана")
    csv_payload = await export_statistics_csv()
    _assert(csv_payload.startswith(b"\xef\xbb\xbf") and len(csv_payload) > 100, "CSV статистики не сформирован")

    # Telegram ограничивает callback_data 64 байтами: проверяем новые клавиатуры.
    keyboards = [work_hub_keyboard(12), ticket_action_keyboard(await get_ticket_by_id(assignment_ticket), {"telegram_id": requester, "role": "purchasing"}, False)]
    for keyboard in keyboards:
        for row in keyboard.inline_keyboard:
            for button in row:
                callback = getattr(button, "callback_data", None)
                if callback:
                    _assert(len(callback.encode("utf-8")) <= 64, f"Слишком длинный callback_data: {callback}")

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
