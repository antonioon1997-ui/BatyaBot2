import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.database import init_db
from app.handlers import start, admin, admin_productivity, tickets, system, updater
from app.scheduler import start_scheduler
from app.pending_updates import collect_and_discard_pending_updates
from app.services.update_manager import mark_runtime_ready, deployment_result_watcher


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher()

    dp.include_router(system.router)
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(admin_productivity.router)
    dp.include_router(updater.router)
    dp.include_router(tickets.router)

    start_scheduler(bot)

    await bot.delete_webhook(drop_pending_updates=False)
    await collect_and_discard_pending_updates(bot)
    await bot.get_me()
    mark_runtime_ready()
    asyncio.create_task(deployment_result_watcher(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())