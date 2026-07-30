from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from app.config import settings
from app.utils import moscow_now

logger = logging.getLogger(__name__)


def _create_sqlite_backup(source_path: Path, destination_path: Path) -> None:
    """Создаёт согласованный снимок SQLite без остановки работающего бота."""
    temp_path = destination_path.with_suffix(destination_path.suffix + ".tmp")
    temp_path.unlink(missing_ok=True)

    source = sqlite3.connect(str(source_path), timeout=10)
    destination = sqlite3.connect(str(temp_path), timeout=10)
    try:
        source.execute("PRAGMA busy_timeout = 10000")
        source.backup(destination, pages=256, sleep=0.02)
        row = destination.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise RuntimeError(f"Проверка резервной копии SQLite не пройдена: {row}")
        destination.commit()
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        destination.close()
        source.close()

    os.replace(temp_path, destination_path)


async def create_database_backup(keep_last: int = 10) -> Path:
    """Создаёт атомарную проверенную копию SQLite и оставляет последние файлы."""
    database_path = Path(settings.database_path).resolve()
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = moscow_now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    destination = backup_dir / f"bot_{timestamp}.db"

    if not database_path.exists():
        raise FileNotFoundError(f"База данных не найдена: {database_path}")

    await asyncio.to_thread(_create_sqlite_backup, database_path, destination)

    backups = sorted(
        backup_dir.glob("bot_*.db"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[max(1, keep_last):]:
        try:
            old_backup.unlink()
        except OSError:
            logger.exception("Не удалось удалить старую резервную копию %s", old_backup)

    logger.info("Создана и проверена резервная копия базы: %s", destination)
    return destination
