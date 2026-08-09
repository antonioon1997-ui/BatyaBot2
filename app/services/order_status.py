from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from app.config import settings


class OrderStatusUnavailable(RuntimeError):
    """Read-only база OrderExporter недоступна или не прошла базовую проверку."""


@dataclass(frozen=True)
class OrderStatusRecord:
    order_number: str
    ms_status: str
    client_items: tuple[str, ...]
    purchasing_items: tuple[str, ...]


@dataclass(frozen=True)
class OrderStatusLookup:
    record: OrderStatusRecord | None
    stale: bool
    loaded_at: datetime | None
    warning: str | None = None


def build_purchasing_snapshot(
    record: OrderStatusRecord | None,
    *,
    stale: bool = False,
) -> str:
    """Формирует снимок заказа для закупки и карточек тикетов."""
    if record is None:
        text = "Данные заказа не найдены в базе OrderExporter."
    else:
        text = "\n".join(
            [
                f"Статус МС: {record.ms_status}",
                "",
                "Статусы заказов поставщиков:",
                *record.purchasing_items,
            ]
        )

    if stale:
        text += "\n\n⚠️ Снимок OrderExporter давно не обновлялся; показаны последние доступные данные."

    return text


def normalize_order_number(value: object) -> str | None:
    if value is None:
        return None

    text = str(value).strip().lstrip("'")
    if not text:
        return None

    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    text = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"\d{3,20}", text):
        return None
    return text


def normalize_sku(value: object) -> str:
    """Нормализация только для безопасного отображения/сравнения fallback-данных.

    Основные связи в OrderExporter уже построены по sku_normalized, поэтому бот
    не пытается повторно изобретать правила сопоставления SKU.
    """
    return str(value or "").strip()


def extract_order_number_from_query(text: str | None) -> str | None:
    if not text:
        return None

    stripped = str(text).strip()
    direct = normalize_order_number(stripped)
    if direct:
        return direct

    match = re.search(r"(?<!\d)(\d{3,20})(?!\d)", stripped)
    if not match:
        return None
    return normalize_order_number(match.group(1))


# ---------------------------------------------------------------------------
# Чистые helper-функции оставлены для обратной совместимости self-test и
# старых импортов. Данные из Google Sheets BatyaBot2 больше не загружает.
# ---------------------------------------------------------------------------

def _split_cell_lines(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in text.split("\n") if line.strip()]


def _supplier_names(value: object) -> set[str]:
    result: set[str] = set()
    for line in _split_cell_lines(value):
        for item in line.split(";"):
            normalized = item.strip().casefold()
            if normalized:
                result.add(normalized)
    return result


def _remove_supplier_segment(line: str, suppliers: set[str]) -> str:
    parts = [part.strip() for part in line.split(",")]
    if not suppliers or len(parts) < 3:
        return ", ".join(part for part in parts if part)
    for index, part in enumerate(parts):
        if part.casefold() in suppliers:
            del parts[index]
            break
    return ", ".join(part for part in parts if part)


def _make_missing_supplier_order_explicit(line: str) -> str:
    line = re.sub(
        r"(?iu)\bзаказ\s+не\s+указан\b",
        "номер заказа поставщика не указан",
        line,
    )
    if re.search(r"(?iu)\bзаказ\b", line) or "номер заказа поставщика" in line.casefold():
        return line
    parts = [part.strip() for part in line.split(",") if part.strip()]
    marker = "номер заказа поставщика не указан"
    if len(parts) >= 3:
        parts.insert(2, marker)
    else:
        parts.append(marker)
    return ", ".join(parts)


def format_purchasing_lines(raw_value: object, supplier_value: object = None) -> list[str]:
    suppliers = _supplier_names(supplier_value)
    result: list[str] = []
    for raw_line in _split_cell_lines(raw_value):
        result.append(_make_missing_supplier_order_explicit(_remove_supplier_segment(raw_line, suppliers)))
    return result


def _client_lines(status_value: object, sku_value: object) -> list[str]:
    lines = _split_cell_lines(status_value)
    if lines:
        return lines
    return [f"{line}, статус не указан" for line in _split_cell_lines(sku_value)]


def _purchasing_lines(
    purchasing_value: object,
    supplier_value: object,
    client_value: object,
    sku_value: object,
) -> list[str]:
    lines = format_purchasing_lines(purchasing_value, supplier_value)
    if lines:
        return lines
    fallback = _split_cell_lines(client_value) or _split_cell_lines(sku_value)
    return [_make_missing_supplier_order_explicit(line) for line in fallback]


def build_order_status_index(rows: Iterable[Iterable[object]]) -> dict[str, OrderStatusRecord]:
    """Совместимый parser старого A:F формата. Сеть/Google не использует."""
    builders: dict[str, dict[str, object]] = {}
    for row in rows:
        values = list(row)
        values.extend([""] * (6 - len(values)))
        order_number = normalize_order_number(values[0])
        if not order_number:
            continue
        builder = builders.setdefault(
            order_number,
            {"ms_status": "", "client_items": [], "purchasing_items": []},
        )
        ms_status = str(values[1]).strip()
        if ms_status and not builder["ms_status"]:
            builder["ms_status"] = ms_status
        builder["client_items"].extend(_client_lines(values[3], values[2]))
        builder["purchasing_items"].extend(
            _purchasing_lines(values[5], values[4], values[3], values[2])
        )
    return {
        order_number: OrderStatusRecord(
            order_number=order_number,
            ms_status=str(data["ms_status"] or "Статус не указан"),
            client_items=tuple(data["client_items"] or ["Данные о товарах не указаны"]),
            purchasing_items=tuple(data["purchasing_items"] or ["Данные по заказам поставщиков не указаны"]),
        )
        for order_number, data in builders.items()
    }


def _order_db_path() -> Path:
    return Path(settings.order_database_path).expanduser()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise OrderStatusUnavailable(
            f"Не найдена база OrderExporter: {path}. Проверь ORDER_DATABASE_PATH и синхронизацию на VPS."
        )
    try:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            timeout=30,
        )
    except sqlite3.Error as exc:
        raise OrderStatusUnavailable(f"Не удалось открыть базу OrderExporter только для чтения: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _format_quantity(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "кол-во не указано"
    if re.search(r"[A-Za-zА-Яа-я]", raw):
        return raw
    normalized = raw.replace(",", ".")
    try:
        number = Decimal(normalized)
        if number == number.to_integral_value():
            display = str(int(number))
        else:
            display = format(number.normalize(), "f").rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError):
        display = raw
    return f"{display} шт"


def _format_supplier_event_time(value: object) -> str | None:
    """Короткая дата события поставщика для пользовательских карточек.

    OrderExporter хранит ISO timestamp. Если timestamp timezone-aware, приводим
    его к МСК; naive-значение не сдвигаем, чтобы не исказить старые записи.
    """
    raw = str(value or "").strip()
    if not raw:
        return None

    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=3)))
    return parsed.strftime("%d.%m.%Y %H:%M")


def _supplier_status_with_milestone(row: sqlite3.Row) -> str:
    """Текущий статус + релевантная дата прихода/выдачи из OrderExporter."""
    status = str(row["raw_status"] or row["normalized_status"] or "статус не указан").strip()
    normalized = str(row["normalized_status"] or "").strip().casefold()

    label: str | None = None
    event_value: object = None
    if normalized == "issued":
        label = "дата выдачи"
        event_value = row["issued_detected_at"]
    elif normalized == "ready":
        label = "Приход на склад"
        event_value = row["ready_detected_at"]

    formatted = _format_supplier_event_time(event_value)
    if label and formatted:
        return f"{status} — {label} {formatted}"
    return status


def _parse_sync_time(value: object, *, fallback_timestamp: float) -> datetime:
    raw = str(value or "").strip()
    if raw:
        candidate = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                # OrderExporter и бот находятся в одной инфраструктуре, но для
                # вычисления возраста безопаснее считать naive timestamp UTC.
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback_timestamp, tz=timezone.utc)


def _latest_success(connection: sqlite3.Connection, path: Path) -> tuple[datetime, str | None]:
    try:
        row = connection.execute(
            """
            SELECT id, finished_at, app_version
            FROM sync_runs
            WHERE status = 'SUCCESS'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise OrderStatusUnavailable(f"В базе OrderExporter недоступна таблица sync_runs: {exc}") from exc

    fallback = path.stat().st_mtime
    if row is None:
        return datetime.fromtimestamp(fallback, tz=timezone.utc), "В базе нет успешного sync_runs."
    return _parse_sync_time(row["finished_at"], fallback_timestamp=fallback), None


def _load_order_sync(order_number: str) -> OrderStatusLookup:
    path = _order_db_path()
    connection = _connect_readonly(path)
    try:
        loaded_at, sync_warning = _latest_success(connection, path)
        age_seconds = max(0.0, (datetime.now(timezone.utc) - loaded_at).total_seconds())
        stale = age_seconds > settings.order_database_stale_after_seconds
        warning_parts: list[str] = []
        if sync_warning:
            warning_parts.append(sync_warning)
        if stale:
            warning_parts.append(
                f"Последний успешный снимок OrderExporter старше {settings.order_database_stale_after_seconds // 3600 or 1} ч."
            )

        try:
            order = connection.execute(
                """
                SELECT ms_order_id, bs_order_number, status_name, active_in_selection
                FROM moysklad_orders
                WHERE bs_order_number = ?
                ORDER BY active_in_selection DESC, updated_at_ms DESC
                LIMIT 1
                """,
                (order_number,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise OrderStatusUnavailable(f"Не удалось прочитать moysklad_orders: {exc}") from exc

        if order is None:
            return OrderStatusLookup(
                record=None,
                stale=stale,
                loaded_at=loaded_at,
                warning=" ".join(warning_parts) or None,
            )

        try:
            items = connection.execute(
                """
                SELECT sku_raw, sku_normalized, quantity_text, name
                FROM moysklad_order_items
                WHERE ms_order_id = ? AND active = 1
                ORDER BY sku_normalized, ms_position_id
                """,
                (order["ms_order_id"],),
            ).fetchall()
            supplier_rows = connection.execute(
                """
                SELECT
                    cso.provider,
                    cso.external_order_id,
                    cso.sku_raw,
                    cso.sku_normalized,
                    sos.quantity_text AS assigned_quantity,
                    cso.source_quantity_text,
                    cso.effective_quantity_text,
                    cso.raw_status,
                    cso.normalized_status,
                    ssc.ready_detected_at,
                    ssc.issued_detected_at
                FROM current_supplier_statuses cso
                LEFT JOIN supplier_status_current ssc
                  ON ssc.provider = cso.provider
                 AND ssc.external_order_id = cso.external_order_id
                JOIN supplier_orders so
                  ON so.provider = cso.provider
                 AND so.external_order_id = cso.external_order_id
                JOIN supplier_order_state sos
                  ON sos.supplier_order_id = so.id
                 AND sos.bs_order_number = cso.bs_order_number
                 AND sos.active = 1
                WHERE cso.bs_order_number = ?
                ORDER BY cso.sku_normalized, cso.provider, cso.external_order_id
                """,
                (order_number,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise OrderStatusUnavailable(f"Не удалось прочитать позиции/статусы OrderExporter: {exc}") from exc

        suppliers_by_sku: dict[str, list[sqlite3.Row]] = {}
        for row in supplier_rows:
            sku = normalize_sku(row["sku_normalized"] or row["sku_raw"])
            if sku:
                suppliers_by_sku.setdefault(sku, []).append(row)

        client_lines: list[str] = []
        purchasing_lines: list[str] = []
        seen_supplier_keys: set[tuple[str, str, str]] = set()

        for item in items:
            match_sku = normalize_sku(item["sku_normalized"] or item["sku_raw"])
            display_sku = normalize_sku(item["sku_raw"] or match_sku) or "SKU не указан"
            quantity = _format_quantity(item["quantity_text"])
            related = suppliers_by_sku.get(match_sku, []) if match_sku else []

            statuses: list[str] = []
            for supplier in related:
                status = _supplier_status_with_milestone(supplier)
                if status and status not in statuses:
                    statuses.append(status)
            client_status = " / ".join(statuses) if statuses else "статус поставщика не указан"
            client_lines.append(f"{display_sku}, {quantity}, {client_status}")

            if not related:
                purchasing_lines.append(
                    f"{display_sku}, {quantity}, номер заказа поставщика не указан, статус поставщика не указан"
                )
                continue

            for supplier in related:
                external_order_id = str(supplier["external_order_id"] or "").strip()
                provider = str(supplier["provider"] or "").strip()
                dedup_key = (provider, external_order_id, match_sku or display_sku)
                if dedup_key in seen_supplier_keys:
                    continue
                seen_supplier_keys.add(dedup_key)
                # Количество в основной строке — именно назначение этого заказа
                # поставщика конкретному BS. Остаток из live_data сюда не подменяем.
                supplier_qty = _format_quantity(supplier["assigned_quantity"])
                status = _supplier_status_with_milestone(supplier)
                order_part = (
                    f"заказ {external_order_id}"
                    if external_order_id
                    else "номер заказа поставщика не указан"
                )
                line = f"{display_sku}, {supplier_qty}, {order_part}, {status}"
                if (
                    provider.casefold() == "privoz"
                    and "выдан частично" in status.casefold()
                    and str(supplier["effective_quantity_text"] or "").strip()
                ):
                    remainder = _format_quantity(supplier["effective_quantity_text"])
                    line += f" (остаток у поставщика: {remainder})"
                purchasing_lines.append(line)

        # На случай, если в current_supplier_statuses есть корректная активная связь,
        # но позиция МойСклад временно отсутствует в выборке, не теряем её из закупочной карточки.
        for supplier in supplier_rows:
            match_sku = normalize_sku(supplier["sku_normalized"] or supplier["sku_raw"])
            display_sku = normalize_sku(supplier["sku_raw"] or match_sku) or "SKU не указан"
            external_order_id = str(supplier["external_order_id"] or "").strip()
            provider = str(supplier["provider"] or "").strip()
            dedup_key = (provider, external_order_id, match_sku or display_sku)
            if dedup_key in seen_supplier_keys:
                continue
            seen_supplier_keys.add(dedup_key)
            quantity = _format_quantity(supplier["assigned_quantity"])
            status = _supplier_status_with_milestone(supplier)
            order_part = f"заказ {external_order_id}" if external_order_id else "номер заказа поставщика не указан"
            line = f"{display_sku}, {quantity}, {order_part}, {status}"
            if (
                provider.casefold() == "privoz"
                and "выдан частично" in status.casefold()
                and str(supplier["effective_quantity_text"] or "").strip()
            ):
                remainder = _format_quantity(supplier["effective_quantity_text"])
                line += f" (остаток у поставщика: {remainder})"
            purchasing_lines.append(line)

        record = OrderStatusRecord(
            order_number=str(order["bs_order_number"]),
            ms_status=str(order["status_name"] or "Статус не указан"),
            client_items=tuple(client_lines or ["В заказе нет активных позиций."]),
            purchasing_items=tuple(purchasing_lines or ["Заказы поставщиков для этого BS не найдены."]),
        )
        return OrderStatusLookup(
            record=record,
            stale=stale,
            loaded_at=loaded_at,
            warning=" ".join(warning_parts) or None,
        )
    finally:
        connection.close()


async def get_order_status(order_number: str, force_refresh: bool = False) -> OrderStatusLookup:
    del force_refresh  # локальный read-only снимок читается напрямую; сетевого кэша больше нет
    normalized = normalize_order_number(order_number)
    if not normalized:
        raise ValueError("Некорректный номер заказа")
    try:
        return await asyncio.to_thread(_load_order_sync, normalized)
    except OrderStatusUnavailable:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise OrderStatusUnavailable(str(exc)) from exc


async def clear_order_status_cache() -> None:
    """Оставлено для API-совместимости: у локальной SQLite больше нет сетевого кэша."""
    return None


def _order_database_health_sync() -> dict[str, object]:
    path = _order_db_path()
    result: dict[str, object] = {
        "path": str(path),
        "available": False,
        "last_sync": None,
        "stale": False,
        "error": None,
    }
    try:
        connection = _connect_readonly(path)
        try:
            loaded_at, warning = _latest_success(connection, path)
            connection.execute("SELECT 1 FROM moysklad_orders LIMIT 1").fetchone()
            connection.execute("SELECT 1 FROM current_supplier_statuses LIMIT 1").fetchone()
        finally:
            connection.close()
        age = max(0.0, (datetime.now(timezone.utc) - loaded_at).total_seconds())
        result.update(
            available=True,
            last_sync=loaded_at,
            stale=age > settings.order_database_stale_after_seconds,
            error=warning,
        )
    except Exception as exc:
        result["error"] = str(exc)
    return result


async def get_order_database_health() -> dict[str, object]:
    return await asyncio.to_thread(_order_database_health_sync)
