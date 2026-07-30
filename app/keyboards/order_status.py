from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def order_status_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="order_status_cancel",
                )
            ]
        ]
    )


def order_status_result_keyboard(
    order_number: str,
    *,
    allow_question: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if allow_question:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❓ Задать вопрос по заказу",
                    callback_data=f"order_status_ask:{order_number}",
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="🔎 Проверить другой заказ",
                    callback_data="order_status_start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="order_status_cancel",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_status_unavailable_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Повторить",
                    callback_data="order_status_start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="order_status_cancel",
                )
            ],
        ]
    )
