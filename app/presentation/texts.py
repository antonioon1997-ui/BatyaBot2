from __future__ import annotations

STRICT = "strict"
FRIENDLY = "friendly"
ALLOWED_STYLES = {STRICT, FRIENDLY}

MESSAGES: dict[str, dict[str, str]] = {
    "access_confirmed": {
        STRICT: "Доступ подтверждён. Нижнее меню обновлено.",
        FRIENDLY: "Готово, доступ подтверждён. Нижнее меню обновлено 👍",
    },
    "main_menu_title": {
        STRICT: "Главное меню:",
        FRIENDLY: "Что нужно сделать?",
    },
    "no_tickets": {
        STRICT: "Тикетов нет.",
        FRIENDLY: "Пока всё спокойно — подходящих тикетов нет 😊",
    },
    "no_archive_tickets": {
        STRICT: "В этом разделе тикетов нет.",
        FRIENDLY: "Здесь пока пусто — подходящих тикетов нет.",
    },
    "unknown_command": {
        STRICT: "Команда не распознана. Используйте кнопки меню.",
        FRIENDLY: "Не понял это сообщение. Попробуйте выбрать нужное действие кнопками меню.",
    },
    "feedback_saved": {
        STRICT: "Сообщение отправлено администратору.",
        FRIENDLY: "Спасибо! Сообщение отправлено — оно поможет сделать работу удобнее 👍",
    },
    "question_saved": {
        STRICT: "Вопрос отправлен администратору.",
        FRIENDLY: "Вопрос отправлен. Администратор увидит его и ответит вам.",
    },
    "generic_error": {
        STRICT: "Произошла внутренняя ошибка. Администратор получит сведения о ней.",
        FRIENDLY: "Что-то пошло не так. Администратор уже получит сведения об ошибке.",
    },
    "style_saved_strict": {
        STRICT: "Выбран строгий стиль сообщений.",
        FRIENDLY: "Выбран строгий стиль сообщений.",
    },
    "style_saved_friendly": {
        STRICT: "Выбран дружелюбный стиль сообщений.",
        FRIENDLY: "Готово — теперь короткие сообщения будут звучать немного живее 🙂",
    },
}


def normalize_style(style: str | None) -> str:
    value = str(style or "").strip().lower()
    return value if value in ALLOWED_STYLES else STRICT


def text_for_style(style: str | None, key: str, **values) -> str:
    selected = normalize_style(style)
    variants = MESSAGES.get(key)
    if not variants:
        raise KeyError(f"Неизвестный ключ текста: {key}")
    template = variants.get(selected) or variants[STRICT]
    return template.format(**values)
