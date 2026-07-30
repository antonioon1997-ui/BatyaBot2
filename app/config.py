import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
    bot_token: str
    admin_id: int
    timezone: str
    reminder_time: str
    database_path: str
    bot_service_name: str
    updater_service_name: str
    google_sheets_credentials: str
    order_status_spreadsheet_id: str
    order_status_sheet_name: str
    order_status_cache_ttl_seconds: int
    order_status_request_timeout_seconds: int


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN")
    admin_id = os.getenv("ADMIN_ID")
    timezone = os.getenv("TIMEZONE", "Europe/Moscow")
    reminder_time = os.getenv("REMINDER_TIME", "08:50")
    database_path = os.getenv("DATABASE_PATH", "bot.db")
    bot_service_name = os.getenv("BOT_SERVICE_NAME", "batyabot2.service")
    updater_service_name = os.getenv("UPDATER_SERVICE_NAME", "batyabot2-updater.service")
    google_sheets_credentials = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "").strip()
    order_status_spreadsheet_id = os.getenv("ORDER_STATUS_SPREADSHEET_ID", "").strip()
    order_status_sheet_name = os.getenv("ORDER_STATUS_SHEET_NAME", "NEW_API_Заказы").strip()

    try:
        order_status_cache_ttl_seconds = max(15, int(os.getenv("ORDER_STATUS_CACHE_TTL_SECONDS", "60")))
    except ValueError:
        order_status_cache_ttl_seconds = 60

    try:
        order_status_request_timeout_seconds = max(5, int(os.getenv("ORDER_STATUS_REQUEST_TIMEOUT_SECONDS", "20")))
    except ValueError:
        order_status_request_timeout_seconds = 20

    if not bot_token:
        raise ValueError("Не указан BOT_TOKEN в файле .env")

    if not admin_id:
        raise ValueError("Не указан ADMIN_ID в файле .env")

    return Settings(
        bot_token=bot_token,
        admin_id=int(admin_id),
        timezone=timezone,
        reminder_time=reminder_time,
        database_path=database_path,
        bot_service_name=bot_service_name,
        updater_service_name=updater_service_name,
        google_sheets_credentials=google_sheets_credentials,
        order_status_spreadsheet_id=order_status_spreadsheet_id,
        order_status_sheet_name=order_status_sheet_name,
        order_status_cache_ttl_seconds=order_status_cache_ttl_seconds,
        order_status_request_timeout_seconds=order_status_request_timeout_seconds
    )


settings = load_settings()