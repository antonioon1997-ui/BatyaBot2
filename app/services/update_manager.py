from __future__ import annotations

import asyncio
import compileall
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterable

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

from app.config import settings
from app.utils import html_escape, moscow_now, moscow_now_iso

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPDATES_DIR = PROJECT_ROOT / "updates"
INCOMING_DIR = UPDATES_DIR / "incoming"
STAGING_DIR = UPDATES_DIR / "staging"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
JOB_FILE = UPDATES_DIR / "pending_job.json"
RESULT_FILE = UPDATES_DIR / "deployment_result.json"
READY_FILE = RUNTIME_DIR / "ready.json"
UPDATE_HISTORY_FILE = RUNTIME_DIR / "update_history.json"
VERSION_FILE = PROJECT_ROOT / "VERSION"

MAX_ARCHIVE_SIZE = 30 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 80 * 1024 * 1024
MAX_FILES = 500

FORBIDDEN_PARTS = {
    ".env", "bot.db", "venv", ".venv", "backups", "deploy_backups",
    "logs", "updates", "runtime", ".git", "__pycache__", "snap",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".so", ".dll", ".exe", ".sh", ".bat", ".cmd", ".ps1"}
ROOT_ALLOWED = {"main.py", "requirements.txt", "update_manifest.json"}


@dataclass
class UpdateInspection:
    archive_path: Path
    staging_path: Path
    manifest: dict
    files: list[str]
    new_files: list[str]
    changed_files: list[str]
    unchanged_files: list[str]
    delete_files: list[str]

    @property
    def release_notes(self) -> list[str]:
        notes = self.manifest.get("release_notes", [])
        return [str(item).strip() for item in notes if str(item).strip()]


def ensure_update_directories() -> None:
    for path in (UPDATES_DIR, INCOMING_DIR, STAGING_DIR, RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)


def get_current_version() -> str:
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
        return value or "1.4"
    except FileNotFoundError:
        return "1.4"


def _is_allowed_project_path(path: PurePosixPath) -> bool:
    parts = path.parts
    if not parts:
        return False
    if any(part in FORBIDDEN_PARTS for part in parts):
        return False
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return False
    if len(parts) == 1:
        return path.name in ROOT_ALLOWED
    return parts[0] == "app" and path.suffix.lower() == ".py"


def _safe_relative_path(raw_name: str) -> PurePosixPath:
    normalized = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts:
        raise ValueError(f"Недопустимый путь: {raw_name}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Опасный путь: {raw_name}")
    if ":" in path.parts[0]:
        raise ValueError(f"Абсолютный Windows-путь запрещён: {raw_name}")
    return path


def _strip_single_wrapper(paths: list[PurePosixPath]) -> tuple[list[PurePosixPath], str | None]:
    first_parts = {path.parts[0] for path in paths if path.parts}
    if len(first_parts) != 1:
        return paths, None
    wrapper = next(iter(first_parts))
    if wrapper in {"app", "main.py", "requirements.txt", "update_manifest.json"}:
        return paths, None
    stripped = [PurePosixPath(*path.parts[1:]) for path in paths]
    if all(path.parts for path in stripped):
        return stripped, wrapper
    return paths, None


def _read_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("В архиве отсутствует обязательный update_manifest.json") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"update_manifest.json содержит ошибку JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ValueError("update_manifest.json должен содержать JSON-объект")

    notes = manifest.get("release_notes")
    if not isinstance(notes, list) or not any(str(item).strip() for item in notes):
        raise ValueError("В update_manifest.json нужен непустой список release_notes")

    major_update = manifest.get("major_update", False)
    if not isinstance(major_update, bool):
        raise ValueError("Поле major_update должно быть true или false")

    delete = manifest.get("delete", [])
    if not isinstance(delete, list):
        raise ValueError("Поле delete должно быть списком")

    return manifest


def _run_candidate_selftest(candidate: Path) -> None:
    selftest_file = candidate / "app" / "selftest.py"
    if not selftest_file.is_file():
        raise ValueError("В проекте отсутствует обязательный модуль app/selftest.py")

    with tempfile.TemporaryDirectory(prefix="batyabot_selftest_") as tmp:
        env = os.environ.copy()
        env.update({
            "BATYABOT_SELFTEST": "1",
            "BOT_TOKEN": "123456:SELFTEST_TOKEN",
            "ADMIN_ID": "1",
            "DATABASE_PATH": str(Path(tmp) / "selftest.db"),
            "TIMEZONE": "Europe/Moscow",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        process = subprocess.run(
            [sys.executable, "-m", "app.selftest"],
            cwd=candidate,
            env=env,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        if process.returncode != 0 or "SELFTEST_OK" not in process.stdout:
            detail = (process.stderr or process.stdout or "Самотест завершился без пояснения")[-5000:]
            raise ValueError("Функциональный самотест обновления не пройден:\n" + detail)


def _validate_delete_paths(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        path = _safe_relative_path(str(value))
        if path.name == "update_manifest.json" or not _is_allowed_project_path(path):
            raise ValueError(f"Удаление пути запрещено: {path.as_posix()}")
        result.append(path.as_posix())
    return result


def inspect_update_archive(archive_path: Path) -> UpdateInspection:
    ensure_update_directories()
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise ValueError("Файл архива не найден")
    if archive_path.stat().st_size > MAX_ARCHIVE_SIZE:
        raise ValueError("Архив больше допустимых 30 МБ")
    if not zipfile.is_zipfile(archive_path):
        raise ValueError("Файл не является корректным ZIP-архивом")

    staging_path = STAGING_DIR / f"stage_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    staging_path.mkdir(parents=True, exist_ok=False)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            file_infos = [info for info in infos if not info.is_dir()]
            if not file_infos:
                raise ValueError("Архив пуст")
            if len(file_infos) > MAX_FILES:
                raise ValueError(f"В архиве больше {MAX_FILES} файлов")
            if any(info.flag_bits & 0x1 for info in file_infos):
                raise ValueError("Зашифрованные ZIP-файлы не поддерживаются")
            total_size = sum(info.file_size for info in file_infos)
            if total_size > MAX_UNCOMPRESSED_SIZE:
                raise ValueError("Распакованный архив превышает 80 МБ")

            original_paths = [_safe_relative_path(info.filename) for info in file_infos]
            normalized_paths, wrapper = _strip_single_wrapper(original_paths)
            errors: list[str] = []

            for info, path in zip(file_infos, normalized_paths):
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if unix_mode and stat.S_ISLNK(unix_mode):
                    errors.append(f"Символическая ссылка запрещена: {path.as_posix()}")
                    continue
                if not _is_allowed_project_path(path):
                    errors.append(f"Запрещённый или лишний файл: {path.as_posix()}")

            if errors:
                preview = "\n".join(f"• {item}" for item in errors[:30])
                extra = len(errors) - 30
                if extra > 0:
                    preview += f"\n• …и ещё {extra}"
                raise ValueError("Архив содержит файлы, препятствующие обновлению:\n" + preview)

            for info, path in zip(file_infos, normalized_paths):
                target = staging_path / Path(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

        manifest = _read_manifest(staging_path / "update_manifest.json")
        delete_files = _validate_delete_paths(manifest.get("delete", []))

        files = sorted(
            path.relative_to(staging_path).as_posix()
            for path in staging_path.rglob("*")
            if path.is_file() and path.name != "update_manifest.json"
        )
        if not files and not delete_files:
            raise ValueError("Архив не содержит файлов для обновления")

        new_files: list[str] = []
        changed_files: list[str] = []
        unchanged_files: list[str] = []
        for rel in files:
            source = staging_path / rel
            destination = PROJECT_ROOT / rel
            if not destination.exists():
                new_files.append(rel)
            elif source.read_bytes() == destination.read_bytes():
                unchanged_files.append(rel)
            else:
                changed_files.append(rel)

        # Проверяем итоговое дерево, а не только отдельные файлы архива.
        with tempfile.TemporaryDirectory(prefix="batyabot_candidate_") as tmp:
            candidate = Path(tmp)
            for root_name in ("app",):
                source_root = PROJECT_ROOT / root_name
                if source_root.exists():
                    shutil.copytree(
                        source_root,
                        candidate / root_name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                    )
            for root_file in ("main.py",):
                source_file = PROJECT_ROOT / root_file
                if source_file.exists():
                    shutil.copy2(source_file, candidate / root_file)
            for rel in files:
                source = staging_path / rel
                destination = candidate / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            for rel in delete_files:
                target = candidate / rel
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            if not (candidate / "main.py").exists() or not (candidate / "app").is_dir():
                raise ValueError("После обновления отсутствуют main.py или папка app")
            if not compileall.compile_dir(candidate, quiet=1, force=True):
                raise ValueError("Синтаксическая проверка Python-файлов завершилась ошибкой")
            _run_candidate_selftest(candidate)

        return UpdateInspection(
            archive_path=archive_path,
            staging_path=staging_path,
            manifest=manifest,
            files=files,
            new_files=new_files,
            changed_files=changed_files,
            unchanged_files=unchanged_files,
            delete_files=delete_files,
        )
    except Exception:
        shutil.rmtree(staging_path, ignore_errors=True)
        raise


def write_pending_job(inspection: UpdateInspection, requested_by: int) -> None:
    ensure_update_directories()
    payload = {
        "created_at": moscow_now_iso(timespec="seconds"),
        "requested_by": int(requested_by),
        "project_root": str(PROJECT_ROOT),
        "archive_path": str(inspection.archive_path),
        "staging_path": str(inspection.staging_path),
        "files": inspection.files,
        "delete": inspection.delete_files,
        "release_notes": inspection.release_notes,
        "major_update": bool(inspection.manifest.get("major_update", False)),
        "current_version": get_current_version(),
    }
    tmp = JOB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, JOB_FILE)


async def start_external_updater() -> tuple[bool, str]:
    systemctl_command = [
        "systemctl", "start", "--no-block", settings.updater_service_name,
    ]
    command = systemctl_command if os.geteuid() == 0 else ["sudo", "-n", *systemctl_command]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except FileNotFoundError:
        return False, "Команда sudo/systemctl не найдена"
    except Exception as exc:
        return False, f"Не удалось запустить обновлятор: {type(exc).__name__}: {exc}"

    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
        return False, detail or f"systemctl завершился с кодом {process.returncode}"
    return True, "Обновлятор запущен"


def mark_runtime_ready() -> None:
    ensure_update_directories()
    payload = {
        "ready_at": moscow_now_iso(timespec="seconds"),
        "pid": os.getpid(),
        "version": get_current_version(),
    }
    tmp = READY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, READY_FILE)


async def _send_update_message_with_retry(
    bot,
    chat_id: int,
    text: str,
    *,
    attempts: int = 5,
) -> bool:
    """Отправляет итог обновления с повторами после перезапуска сети/polling."""
    import logging

    logger = logging.getLogger(__name__)
    for attempt in range(1, attempts + 1):
        try:
            await bot.send_message(chat_id, text)
            return True
        except TelegramRetryAfter as exc:
            delay = max(float(exc.retry_after), 1.0) + 1.0
            logger.warning(
                "Telegram просит повторить уведомление об обновлении для %s через %.1f сек.",
                chat_id,
                delay,
            )
            await asyncio.sleep(delay)
        except (TelegramNetworkError, OSError) as exc:
            if attempt >= attempts:
                logger.exception(
                    "Не удалось доставить итог обновления пользователю %s после %s попыток",
                    chat_id,
                    attempts,
                )
                return False
            delay = min(2 ** attempt, 15)
            logger.warning(
                "Временная сетевая ошибка при уведомлении %s: %s. Повтор через %s сек.",
                chat_id,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
        except Exception:
            logger.exception("Ошибка отправки итога обновления пользователю %s", chat_id)
            if attempt >= attempts:
                return False
            await asyncio.sleep(min(2 ** attempt, 15))
    return False


def _rewrite_result_payload(payload: dict) -> None:
    tmp = RESULT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, RESULT_FILE)


def _append_successful_update_history(payload: dict) -> None:
    ensure_update_directories()
    history: list[dict] = []

    if UPDATE_HISTORY_FILE.exists():
        try:
            loaded = json.loads(UPDATE_HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = [item for item in loaded if isinstance(item, dict)]
        except (json.JSONDecodeError, OSError):
            # Повреждённую историю сохраняем рядом для ручной диагностики,
            # но не позволяем ей сломать запуск или обработку обновления.
            try:
                damaged = UPDATE_HISTORY_FILE.with_name(
                    f"update_history.corrupt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                )
                shutil.copy2(UPDATE_HISTORY_FILE, damaged)
            except OSError:
                pass
            history = []

    version = str(payload.get("version", "")).strip()
    if not version:
        return

    if any(str(item.get("version", "")).strip() == version for item in history):
        return

    finished_at = str(payload.get("finished_at", "")).strip()
    date_value = moscow_now().strftime("%Y-%m-%d %H:%M:%S МСК")
    changes = [
        str(item).strip()
        for item in payload.get("release_notes", [])
        if str(item).strip()
    ]

    history.append({
        "version": version,
        "date": date_value,
        "changes": changes,
    })

    tmp = UPDATE_HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, UPDATE_HISTORY_FILE)


async def deployment_result_watcher(bot) -> None:
    ensure_update_directories()
    import logging

    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(2)
        if not RESULT_FILE.exists():
            continue
        try:
            payload = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            status = payload.get("status")
            version = payload.get("version", get_current_version())
            notes = [str(item).strip() for item in payload.get("release_notes", []) if str(item).strip()]

            def notes_for_department(department: str | None) -> list[str]:
                result: list[str] = []
                for item in notes:
                    lowered = item.lower()
                    if lowered.startswith("[client]"):
                        if department == "client":
                            result.append(item[len("[client]"):].strip())
                        continue
                    if lowered.startswith("[purchasing]"):
                        if department == "purchasing":
                            result.append(item[len("[purchasing]"):].strip())
                        continue
                    if lowered.startswith("[all]"):
                        result.append(item[len("[all]"):].strip())
                        continue
                    result.append(item)
                return [value for value in result if value]

            admin_notes = []
            for item in notes:
                clean = item
                for prefix in ("[client]", "[purchasing]", "[all]"):
                    if clean.lower().startswith(prefix):
                        clean = clean[len(prefix):].strip()
                        break
                if clean:
                    admin_notes.append(clean)
            notes_text = "\n".join(f"• {html_escape(item)}" for item in admin_notes) or "• Техническое обновление без пользовательских изменений"
            requested_by = int(payload.get("admin_id") or settings.admin_id)
            primary_admin_id = int(settings.admin_id)

            if status == "success":
                if not payload.get("history_recorded"):
                    _append_successful_update_history(payload)
                    payload["history_recorded"] = True
                    _rewrite_result_payload(payload)

                admin_text = (
                    "✅ <b>Обновление успешно установлено</b>\n\n"
                    f"Версия: <b>{version}</b>\n"
                    f"Изменено файлов: {payload.get('changed_count', 0)}\n"
                    f"Добавлено файлов: {payload.get('new_count', 0)}\n"
                    f"Удалено файлов: {payload.get('deleted_count', 0)}\n"
                    f"Резервная копия: <code>{html_escape(payload.get('backup_name', '—'))}</code>\n\n"
                    f"<b>Что изменилось:</b>\n{notes_text}"
                )
                user_text = (
                    "✅ <b>Техническое обслуживание завершено</b>\n\n"
                    f"Бот обновлён до версии <b>{version}</b> и снова готов к работе.\n\n"
                    f"<b>Что изменилось:</b>\n{notes_text}"
                )
            else:
                admin_text = (
                    "↩️ <b>Обновление не установлено — выполнен автоматический откат</b>\n\n"
                    f"Рабочая версия: <b>{version}</b>\n"
                    f"Причина: <code>{html_escape(str(payload.get('error', 'неизвестная ошибка'))[:2500])}</code>\n"
                    f"Восстановлена копия: <code>{html_escape(payload.get('backup_name', '—'))}</code>"
                )
                user_text = (
                    "✅ <b>Техническое обслуживание завершено</b>\n\n"
                    "Обновление было отменено, восстановлена предыдущая рабочая версия. "
                    "Ботом снова можно пользоваться."
                )

            # Административный итог всегда отправляем первым и с повторами.
            # Если ADMIN_ID и инициатор почему-либо различаются, уведомляем обоих.
            admin_recipients = list(dict.fromkeys([primary_admin_id, requested_by]))
            notified_admins = {int(value) for value in payload.get("notified_admins", [])}
            for admin_id in admin_recipients:
                if admin_id in notified_admins:
                    continue
                delivered = await _send_update_message_with_retry(bot, admin_id, admin_text)
                if not delivered:
                    # RESULT_FILE сохраняем: watcher повторит доставку через несколько секунд.
                    raise RuntimeError(f"Не удалось доставить итог обновления администратору {admin_id}")
                notified_admins.add(admin_id)
                payload["notified_admins"] = sorted(notified_admins)
                _rewrite_result_payload(payload)

            from app.services.users import get_active_users

            users = await get_active_users()
            admin_set = set(admin_recipients)
            for user in users:
                telegram_id = int(user["telegram_id"])
                if telegram_id in admin_set:
                    continue
                from app.domain import department_by_role
                department = department_by_role(user["role"])
                user_notes = notes_for_department(department)
                user_notes_text = "\n".join(f"• {html_escape(item)}" for item in user_notes)
                if status == "success":
                    role_user_text = (
                        "✅ <b>Техническое обслуживание завершено</b>\n\n"
                        f"Бот обновлён до версии <b>{version}</b> и снова готов к работе."
                    )
                    if user_notes_text:
                        role_user_text += f"\n\n<b>Что изменилось:</b>\n{user_notes_text}"
                else:
                    role_user_text = user_text
                await _send_update_message_with_retry(
                    bot,
                    telegram_id,
                    role_user_text,
                    attempts=3,
                )

            RESULT_FILE.unlink(missing_ok=True)
        except Exception:
            logger.exception("Не удалось обработать итог системного обновления; будет повторная попытка")
            await asyncio.sleep(10)

