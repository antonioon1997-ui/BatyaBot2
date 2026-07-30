import asyncio
import aiosqlite
from app.config import settings


async def main():
    db = await aiosqlite.connect(settings.database_path)

    await db.execute(
        """
        INSERT INTO users (
            telegram_id,
            username,
            full_name,
            role,
            is_active,
            updated_at
        )
        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(telegram_id) DO UPDATE SET
            role = excluded.role,
            is_active = 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            settings.admin_id,
            None,
            "Антон",
            "purchaser"
        )
    )

    await db.commit()
    await db.close()

    print("Готово. Админ добавлен как закупщик.")


if __name__ == "__main__":
    asyncio.run(main())