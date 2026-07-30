from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MOSCOW_TIMEZONE_NAME = "Europe/Moscow"
MOSCOW_TZ = ZoneInfo(MOSCOW_TIMEZONE_NAME)


def moscow_now() -> datetime:
    return datetime.now(MOSCOW_TZ)


def moscow_now_iso(*, timespec: str = "seconds") -> str:
    return moscow_now().isoformat(timespec=timespec)


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_moscow_datetime(value, default: str = "—") -> str:
    """Показывает время пользователю в МСК; наивные SQLite-метки считаются UTC."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default

    parsed = _parse_datetime(text)
    if parsed is None:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(MOSCOW_TZ)
    return local.strftime("%d.%m.%Y %H:%M МСК")
