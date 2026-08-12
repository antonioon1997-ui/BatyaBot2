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
from app.services.main_menu_dashboard import build_main_menu_text
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

# 2.6.0 переводит проект на SemVer x.y.z. Первый запуск после старого
# обновлятора всё ещё подтверждается как 2.6, после чего VERSION атомарно
# нормализуется в 2.6.0. Следующие обновления уже выполняет новый worker.
SEMVER_STATE_FILE = RUNTIME_DIR / "semver_migration.json"
EXTERNAL_UPDATER_SOURCE = Path(__file__).with_name("external_updater_worker.py")
EXTERNAL_UPDATER_TARGET = Path(
    os.getenv(
        "BATYABOT_EXTERNAL_UPDATER",
        "/usr/local/lib/batyabot2-updater/batyabot_updater.py",
    )
)

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
        return value or "1.4.0"
    except FileNotFoundError:
        return "1.4.0"


def parse_version(version: str) -> tuple[int, int, int]:
    try:
        parts = [int(part) for part in str(version).strip().split(".")]
        if len(parts) == 2:
            parts.append(0)
        if len(parts) != 3 or any(part < 0 for part in parts):
            raise ValueError
        return parts[0], parts[1], parts[2]
    except Exception:
        return 1, 4, 0


def predict_next_version(version: str, bump: str = "patch") -> str:
    major, minor, patch = parse_version(version)
    bump = str(bump or "patch").strip().lower()
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _manifest_version_bump(manifest: dict) -> str:
    value = str(manifest.get("version_bump") or "").strip().lower()
    if value in {"major", "minor", "patch"}:
        return value
    if bool(manifest.get("major_update", False)):
        return "major"
    return "patch"


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

    version_bump = manifest.get("version_bump")
    if version_bump is not None and str(version_bump).strip().lower() not in {"major", "minor", "patch"}:
        raise ValueError("Поле version_bump должно быть major, minor или patch")
    manifest["version_bump"] = _manifest_version_bump(manifest)

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
        "version_bump": _manifest_version_bump(inspection.manifest),
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


def _read_semver_state() -> dict:
    try:
        payload = json.loads(SEMVER_STATE_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_semver_state(payload: dict) -> None:
    ensure_update_directories()
    tmp = SEMVER_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SEMVER_STATE_FILE)


def ensure_external_updater_worker() -> bool:
    """Синхронизирует root-worker обновлений с SemVer-версией из проекта."""
    try:
        source = EXTERNAL_UPDATER_SOURCE.read_bytes()
        if not source:
            raise RuntimeError("Шаблон внешнего обновлятора пуст")
        # Проверяем синтаксис до замены рабочего worker.
        compile(source.decode("utf-8"), str(EXTERNAL_UPDATER_SOURCE), "exec")
        try:
            if EXTERNAL_UPDATER_TARGET.read_bytes() == source:
                return False
        except FileNotFoundError:
            pass

        EXTERNAL_UPDATER_TARGET.parent.mkdir(parents=True, exist_ok=True)
        if EXTERNAL_UPDATER_TARGET.exists():
            backup = EXTERNAL_UPDATER_TARGET.with_suffix(".pre_semver.bak")
            if not backup.exists():
                shutil.copy2(EXTERNAL_UPDATER_TARGET, backup)
        tmp = EXTERNAL_UPDATER_TARGET.with_suffix(".tmp")
        tmp.write_bytes(source)
        os.chmod(tmp, 0o755)
        os.replace(tmp, EXTERNAL_UPDATER_TARGET)
        return True
    except Exception:
        # Ошибка миграции worker не должна мешать текущему запуску бота,
        # но будет видна в журнале systemd. Следующее обновление без worker
        # запускать не следует.
        import logging
        logging.getLogger(__name__).exception("Не удалось установить SemVer-worker обновлятора")
        return False


def _normalize_started_version(started_version: str) -> str:
    parts = str(started_version).strip().split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return started_version

    normalized = f"{int(parts[0])}.{int(parts[1])}.0"
    _write_semver_state({
        "installer_version": started_version,
        "normalized_version": normalized,
        "completed": False,
        "created_at": moscow_now_iso(timespec="seconds"),
    })
    VERSION_FILE.write_text(normalized + "\n", encoding="utf-8")
    return normalized


def mark_runtime_ready() -> None:
    ensure_update_directories()
    started_version = get_current_version()
    payload = {
        "ready_at": moscow_now_iso(timespec="seconds"),
        "pid": os.getpid(),
        "version": started_version,
    }
    tmp = READY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, READY_FILE)

    # Старый updater, устанавливающий именно 2.6.0, ожидает READY=2.6.
    # После подтверждения готовности рабочий VERSION уже становится 2.6.0.
    _normalize_started_version(started_version)


async def _send_update_message_with_retry(
    bot,
    chat_id: int,
    text: str,
    *,
    attempts: int = 5,
    reply_markup=None,
) -> bool:
    """Отправляет итог обновления с повторами после перезапуска сети/polling."""
    import logging

    logger = logging.getLogger(__name__)
    for attempt in range(1, attempts + 1):
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
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

            semver_state = _read_semver_state()
            if (
                status == "success"
                and not semver_state.get("completed")
                and str(payload.get("version")) == str(semver_state.get("installer_version"))
                and semver_state.get("normalized_version")
            ):
                payload["version"] = str(semver_state["normalized_version"])
                payload["version_normalized"] = True
                _rewrite_result_payload(payload)

            version = payload.get("version", get_current_version())
            notes = [str(item).strip() for item in payload.get("release_notes", []) if str(item).strip()]

            def notes_for_department(department: str | None) -> list[str]:
                result: list[str] = []
                for item in notes:
                    lowered = item.lower()
                    if lowered.startswith("[admin]"):
                        continue
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

            admin_notes: list[str] = []
            for item in notes:
                clean = item
                for prefix in ("[client]", "[purchasing]", "[all]", "[admin]"):
                    if clean.lower().startswith(prefix):
                        clean = clean[len(prefix):].strip()
                        break
                if clean:
                    admin_notes.append(clean)
            notes_text = "\n".join(f"• {html_escape(item)}" for item in admin_notes) or "• Техническое обновление без пользовательских изменений"
            requested_by = int(payload.get("admin_id") or settings.admin_id)
            primary_admin_id = int(settings.admin_id)

            if status == "success":
                if not payload.get("semver_worker_installed"):
                    ensure_external_updater_worker()
                    try:
                        worker_ok = (
                            EXTERNAL_UPDATER_TARGET.read_bytes()
                            == EXTERNAL_UPDATER_SOURCE.read_bytes()
                        )
                    except OSError:
                        worker_ok = False
                    if worker_ok:
                        payload["semver_worker_installed"] = True
                        _rewrite_result_payload(payload)
                    else:
                        logger.error("SemVer-worker обновлятора не установлен; следующее обновление требует проверки")

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
            else:
                admin_text = (
                    "↩️ <b>Обновление не установлено — выполнен автоматический откат</b>\n\n"
                    f"Рабочая версия: <b>{version}</b>\n"
                    f"Причина: <code>{html_escape(str(payload.get('error', 'неизвестная ошибка'))[:2500])}</code>\n"
                    f"Восстановлена копия: <code>{html_escape(payload.get('backup_name', '—'))}</code>"
                )

            admin_recipients = list(dict.fromkeys([primary_admin_id, requested_by]))
            notified_admins = {int(value) for value in payload.get("notified_admins", [])}
            for admin_id in admin_recipients:
                if admin_id in notified_admins:
                    continue
                delivered = await _send_update_message_with_retry(bot, admin_id, admin_text)
                if not delivered:
                    raise RuntimeError(f"Не удалось доставить итог обновления администратору {admin_id}")
                notified_admins.add(admin_id)
                payload["notified_admins"] = sorted(notified_admins)
                _rewrite_result_payload(payload)

            from app.domain import department_by_role
            from app.keyboards.common import bottom_menu_for_role, main_menu_for_role
            from app.services.ui_messages import send_ui_text
            from app.services.users import get_active_users

            users = await get_active_users()
            notified_users = {int(value) for value in payload.get("notified_users", [])}
            failed_users = {int(value) for value in payload.get("failed_users", [])}
            admin_set = set(admin_recipients)

            for user in users:
                telegram_id = int(user["telegram_id"])
                if telegram_id in notified_users or telegram_id in failed_users:
                    continue

                department = department_by_role(user["role"])
                user_notes = notes_for_department(department)
                user_notes_text = "\n".join(f"• {html_escape(item)}" for item in user_notes)

                if status == "success":
                    if telegram_id in admin_set:
                        role_user_text = (
                            "🔄 <b>Панель управления обновлена автоматически</b>\n\n"
                            "Новые кнопки уже загружены. Можно продолжать работу.\n\n"
                            "Нижнее быстрое меню сохранено. Если inline-панель потерялась, нажмите «🏠 Меню» или используйте /menu."
                        )
                    else:
                        role_user_text = (
                            "✅ <b>Техническое обслуживание завершено</b>\n\n"
                            f"Бот обновлён до версии <b>{version}</b> и снова готов к работе.\n"
                            "Панель управления обновлена автоматически — можно продолжать работу."
                        )
                        if user_notes_text:
                            role_user_text += f"\n\n<b>Что изменилось:</b>\n{user_notes_text}"
                        role_user_text += (
                            "\n\nНижнее быстрое меню сохранено. Если inline-панель потерялась, нажмите «🏠 Меню» или используйте /menu."
                        )
                else:
                    role_user_text = (
                        "✅ <b>Техническое обслуживание завершено</b>\n\n"
                        "Обновление было отменено, восстановлена предыдущая рабочая версия. "
                        "Панель управления восстановлена автоматически — ботом снова можно пользоваться."
                    )

                delivered = await _send_update_message_with_retry(
                    bot,
                    telegram_id,
                    role_user_text,
                    attempts=3,
                    reply_markup=bottom_menu_for_role(
                        user["role"],
                        is_admin=telegram_id == primary_admin_id,
                    ),
                )
                if not delivered:
                    failed_users.add(telegram_id)
                    payload["failed_users"] = sorted(failed_users)
                    _rewrite_result_payload(payload)
                    continue

                if status == "success":
                    try:
                        menu_text = await build_main_menu_text(telegram_id, user["role"])
                        await send_ui_text(
                            bot,
                            chat_id=telegram_id,
                            text=menu_text,
                            reply_markup=main_menu_for_role(
                                user["role"],
                                is_admin=telegram_id == primary_admin_id,
                            ),
                        )
                    except Exception:
                        logger.exception("Не удалось автоматически открыть inline-меню пользователя %s", telegram_id)

                notified_users.add(telegram_id)
                payload["notified_users"] = sorted(notified_users)
                _rewrite_result_payload(payload)

            if failed_users and not payload.get("failed_users_reported"):
                failed_text = ", ".join(str(value) for value in sorted(failed_users))
                warning_text = (
                    "⚠️ <b>Не всем пользователям удалось обновить меню</b>\n\n"
                    f"Не доставлено: <b>{len(failed_users)}</b>. "
                    "Возможно, эти пользователи заблокировали бота или давно не открывали чат.\n"
                    f"Telegram ID: <code>{html_escape(failed_text)}</code>"
                )
                for admin_id in admin_recipients:
                    await _send_update_message_with_retry(bot, admin_id, warning_text, attempts=3)
                payload["failed_users_reported"] = True
                _rewrite_result_payload(payload)

            if status == "success" and semver_state and not semver_state.get("completed"):
                semver_state["completed"] = True
                semver_state["completed_at"] = moscow_now_iso(timespec="seconds")
                _write_semver_state(semver_state)

            RESULT_FILE.unlink(missing_ok=True)
        except Exception:
            logger.exception("Не удалось обработать итог системного обновления; будет повторная попытка")
            await asyncio.sleep(10)
