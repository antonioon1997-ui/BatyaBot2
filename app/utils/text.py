from __future__ import annotations

from html import escape


def html_escape(value, default: str = "—") -> str:
    """Экранирует динамический текст для Telegram HTML parse mode."""
    if value is None:
        return default
    text = str(value)
    if not text.strip():
        return default
    return escape(text, quote=False)
