from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.database import get_db


@dataclass(slots=True)
class UiContext:
    user_id: int
    view: str = "main"
    list_type: str | None = None
    page: int = 0
    filters: dict[str, Any] | None = None
    search_query: str | None = None
    queue_ids: list[int] | None = None
    current_ticket_id: int | None = None
    current_index: int | None = None
    mode: str = "normal"
    return_view: str | None = None

    @property
    def filters_dict(self) -> dict[str, Any]:
        return dict(self.filters or {})

    @property
    def queue(self) -> list[int]:
        return list(self.queue_ids or [])


def _loads_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _loads_ids(value: str | None) -> list[int]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


async def get_ui_context(user_id: int) -> UiContext:
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT * FROM ui_navigation_state WHERE user_id = ?
            """,
            (int(user_id),),
        )
        row = await cursor.fetchone()
        if not row:
            return UiContext(user_id=int(user_id))
        return UiContext(
            user_id=int(user_id),
            view=str(row["view"] or "main"),
            list_type=str(row["list_type"]) if row["list_type"] else None,
            page=max(int(row["page"] or 0), 0),
            filters=_loads_dict(row["filters_json"]),
            search_query=str(row["search_query"]) if row["search_query"] else None,
            queue_ids=_loads_ids(row["queue_ids_json"]),
            current_ticket_id=int(row["current_ticket_id"]) if row["current_ticket_id"] is not None else None,
            current_index=int(row["current_index"]) if row["current_index"] is not None else None,
            mode=str(row["mode"] or "normal"),
            return_view=str(row["return_view"]) if row["return_view"] else None,
        )
    finally:
        await db.close()


async def set_ui_context(user_id: int, **values: Any) -> UiContext:
    current = await get_ui_context(user_id)
    payload: dict[str, Any] = {
        "view": current.view,
        "list_type": current.list_type,
        "page": current.page,
        "filters": current.filters_dict,
        "search_query": current.search_query,
        "queue_ids": current.queue,
        "current_ticket_id": current.current_ticket_id,
        "current_index": current.current_index,
        "mode": current.mode,
        "return_view": current.return_view,
    }
    payload.update(values)
    payload["page"] = max(int(payload.get("page") or 0), 0)
    payload["mode"] = str(payload.get("mode") or "normal")
    payload["view"] = str(payload.get("view") or "main")
    filters = payload.get("filters") or {}
    queue_ids = payload.get("queue_ids") or []

    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO ui_navigation_state (
                user_id, view, list_type, page, filters_json, search_query,
                queue_ids_json, current_ticket_id, current_index, mode,
                return_view, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                view=excluded.view,
                list_type=excluded.list_type,
                page=excluded.page,
                filters_json=excluded.filters_json,
                search_query=excluded.search_query,
                queue_ids_json=excluded.queue_ids_json,
                current_ticket_id=excluded.current_ticket_id,
                current_index=excluded.current_index,
                mode=excluded.mode,
                return_view=excluded.return_view,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                int(user_id),
                payload["view"],
                payload.get("list_type"),
                payload["page"],
                json.dumps(filters, ensure_ascii=False),
                payload.get("search_query"),
                json.dumps([int(item) for item in queue_ids], ensure_ascii=False),
                payload.get("current_ticket_id"),
                payload.get("current_index"),
                payload["mode"],
                payload.get("return_view"),
            ),
        )
        await db.commit()
    finally:
        await db.close()
    return await get_ui_context(user_id)


async def clear_ui_context(user_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM ui_navigation_state WHERE user_id = ?", (int(user_id),))
        await db.commit()
    finally:
        await db.close()


async def set_ticket_list_context(
    user_id: int,
    *,
    list_type: str,
    page: int,
    queue_ids: list[int],
    filters: dict[str, Any] | None = None,
    search_query: str | None = None,
    mode: str = "normal",
    return_view: str | None = None,
) -> UiContext:
    return await set_ui_context(
        user_id,
        view="ticket_list",
        list_type=list_type,
        page=page,
        filters=filters or {},
        search_query=search_query,
        queue_ids=queue_ids,
        current_ticket_id=None,
        current_index=None,
        mode=mode,
        return_view=return_view,
    )


async def set_ticket_context(
    user_id: int,
    *,
    ticket_id: int,
    current_index: int | None = None,
    mode: str | None = None,
) -> UiContext:
    values: dict[str, Any] = {
        "view": "ticket_card",
        "current_ticket_id": int(ticket_id),
        "current_index": current_index,
    }
    if mode is not None:
        values["mode"] = mode
    return await set_ui_context(user_id, **values)
