from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from app.config import settings


class OrderStatusUnavailable(RuntimeError):
    """Google Sheets недоступен и пригодного кэша ещё нет."""


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


_cache_lock = asyncio.Lock()
_cache_index: dict[str, OrderStatusRecord] = {}
_cache_loaded_monotonic = 0.0
_cache_loaded_at: datetime | None = None
_cache_initialized = False


def normalize_order_number(value: object) -> str | None:
    if value is None:
        return None

    text = str(value).strip().lstrip("'")
    if not text:
        return None

    # Значения из Google Sheets иногда могут выглядеть как 11786.0,
    # если исходная ячейка была числовой и форматирование менялось.
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    text = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"\d{3,20}", text):
        return None

    return text


def extract_order_number_from_query(text: str | None) -> str | None:
    if not text:
        return None

    stripped = str(text).strip()
    direct = normalize_order_number(stripped)
    if direct:
        return direct

    # «Заказ 11786», «№11786», «заказ: 11786» и похожие варианты.
    match = re.search(r"(?<!\d)(\d{3,20})(?!\d)", stripped)
    if not match:
        return None

    return normalize_order_number(match.group(1))


def _split_cell_lines(value: object) -> list[str]:
    if value is None:
        return []

    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in text.split("\n") if line.strip()]


def _supplier_names(value: object) -> set[str]:
    result: set[str] = set()
    for line in _split_cell_lines(value):
        # В столбце E поставщики обычно разделены переносами строк.
        # Точку с запятой также считаем безопасным разделителем, но запятую
        # не используем: она может быть частью названия.
        for item in line.split(";"):
            normalized = item.strip().casefold()
            if normalized:
                result.add(normalized)
    return result


def _remove_supplier_segment(line: str, suppliers: set[str]) -> str:
    parts = [part.strip() for part in line.split(",")]
    if not suppliers or len(parts) < 3:
        return ", ".join(part for part in parts if part)

    remove_index = None
    for index, part in enumerate(parts):
        if part.casefold() in suppliers:
            remove_index = index
            break

    if remove_index is not None:
        del parts[remove_index]

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
    """Убирает название поставщика, сохраняя строки, порядок и номера заказов.

    Значение вида 119861/245415 остаётся единым номером. Одинаковые SKU и
    повторяющиеся строки намеренно не объединяются.
    """
    suppliers = _supplier_names(supplier_value)
    result: list[str] = []

    for raw_line in _split_cell_lines(raw_value):
        without_supplier = _remove_supplier_segment(raw_line, suppliers)
        result.append(_make_missing_supplier_order_explicit(without_supplier))

    return result


def _client_lines(status_value: object, sku_value: object) -> list[str]:
    lines = _split_cell_lines(status_value)
    if lines:
        return lines

    # Если формула статусов временно пуста, не теряем состав заказа.
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
    """Строит быстрый индекс заказов из строк A:F листа Google Sheets."""
    builders: dict[str, dict[str, object]] = {}

    for row in rows:
        values = list(row)
        values.extend([""] * (6 - len(values)))
        order_number = normalize_order_number(values[0])
        if not order_number:
            # Заголовки и пустые строки сюда не попадут.
            continue

        builder = builders.setdefault(
            order_number,
            {
                "ms_status": "",
                "client_items": [],
                "purchasing_items": [],
            },
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
            purchasing_items=tuple(
                data["purchasing_items"]
                or ["Данные по заказам поставщиков не указаны"]
            ),
        )
        for order_number, data in builders.items()
    }


def _fetch_rows_sync() -> list[list[str]]:
    credentials_path = Path(settings.google_sheets_credentials)
    if not settings.google_sheets_credentials or not credentials_path.is_file():
        raise OrderStatusUnavailable(
            "Не найден JSON-ключ Google Sheets. Проверь GOOGLE_SHEETS_CREDENTIALS."
        )
    if not settings.order_status_spreadsheet_id:
        raise OrderStatusUnavailable("Не указан ORDER_STATUS_SPREADSHEET_ID.")
    if not settings.order_status_sheet_name:
        raise OrderStatusUnavailable("Не указан ORDER_STATUS_SHEET_NAME.")

    # Импорты намеренно ленивые: встроенный валидатор обновлений может
    # запустить self-test до установки новых requirements.
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError as exc:
        raise OrderStatusUnavailable(
            "Не установлена библиотека google-auth. Требуется завершить обновление зависимостей."
        ) from exc

    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_path),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    range_name = f"'{settings.order_status_sheet_name}'!A:F"
    encoded_range = quote(range_name, safe="")
    url = (
        "https://sheets.googleapis.com/v4/spreadsheets/"
        f"{settings.order_status_spreadsheet_id}/values/{encoded_range}"
    )

    session = AuthorizedSession(credentials)
    try:
        response = session.get(
            url,
            params={
                "majorDimension": "ROWS",
                "valueRenderOption": "FORMATTED_VALUE",
            },
            timeout=settings.order_status_request_timeout_seconds,
        )
    finally:
        session.close()

    if response.status_code != 200:
        try:
            payload = response.json()
            detail = payload.get("error", {}).get("message") or json.dumps(
                payload, ensure_ascii=False
            )
        except Exception:
            detail = response.text[:1000]
        raise OrderStatusUnavailable(
            f"Google Sheets вернул HTTP {response.status_code}: {detail}"
        )

    payload = response.json()
    values = payload.get("values", [])
    if not isinstance(values, list):
        raise OrderStatusUnavailable("Google Sheets вернул неожиданный формат данных.")

    return values


def _cache_is_fresh(now: float) -> bool:
    return (
        _cache_initialized
        and now - _cache_loaded_monotonic < settings.order_status_cache_ttl_seconds
    )


async def get_order_status(order_number: str, force_refresh: bool = False) -> OrderStatusLookup:
    global _cache_index, _cache_loaded_monotonic, _cache_loaded_at, _cache_initialized

    normalized = normalize_order_number(order_number)
    if not normalized:
        raise ValueError("Некорректный номер заказа")

    now = time.monotonic()
    if not force_refresh and _cache_is_fresh(now):
        return OrderStatusLookup(
            record=_cache_index.get(normalized),
            stale=False,
            loaded_at=_cache_loaded_at,
        )

    async with _cache_lock:
        now = time.monotonic()
        if not force_refresh and _cache_is_fresh(now):
            return OrderStatusLookup(
                record=_cache_index.get(normalized),
                stale=False,
                loaded_at=_cache_loaded_at,
            )

        try:
            rows = await asyncio.to_thread(_fetch_rows_sync)
            new_index = build_order_status_index(rows)
        except Exception as exc:
            if _cache_initialized:
                return OrderStatusLookup(
                    record=_cache_index.get(normalized),
                    stale=True,
                    loaded_at=_cache_loaded_at,
                    warning=str(exc),
                )
            if isinstance(exc, OrderStatusUnavailable):
                raise
            raise OrderStatusUnavailable(str(exc)) from exc

        _cache_index = new_index
        _cache_loaded_monotonic = time.monotonic()
        _cache_loaded_at = datetime.now(timezone.utc)
        _cache_initialized = True

        return OrderStatusLookup(
            record=_cache_index.get(normalized),
            stale=False,
            loaded_at=_cache_loaded_at,
        )


async def clear_order_status_cache() -> None:
    global _cache_index, _cache_loaded_monotonic, _cache_loaded_at, _cache_initialized
    async with _cache_lock:
        _cache_index = {}
        _cache_loaded_monotonic = 0.0
        _cache_loaded_at = None
        _cache_initialized = False
