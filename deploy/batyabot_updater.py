#!/usr/bin/env python3
from __future__ import annotations

import compileall
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_DIR", "/root/BatyaBot/BatyaBot2")).resolve()
BOT_SERVICE = os.environ.get("BOT_SERVICE", "batyabot2.service")
KEEP_BACKUPS = int(os.environ.get("KEEP_DEPLOY_BACKUPS", "10"))
START_TIMEOUT = int(os.environ.get("BOT_START_TIMEOUT", "60"))

UPDATES_DIR = PROJECT_ROOT / "updates"
JOB_FILE = UPDATES_DIR / "pending_job.json"
RESULT_FILE = UPDATES_DIR / "deployment_result.json"
READY_FILE = PROJECT_ROOT / "runtime" / "ready.json"
BACKUPS_DIR = PROJECT_ROOT / "deploy_backups"
VERSION_FILE = PROJECT_ROOT / "VERSION"
PROTECTED_TOP_LEVEL = {
    ".env", "bot.db", "venv", ".venv", "backups", "deploy_backups",
    "logs", "updates", "runtime", ".git", "snap",
}


def log(message: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def run(command: list[str], *, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    log("$ " + " ".join(command))
    return subprocess.run(command, cwd=cwd, check=check, text=True, capture_output=True)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_job() -> dict:
    if not JOB_FILE.exists():
        raise RuntimeError(f"Не найден файл задания {JOB_FILE}")
    data = json.loads(JOB_FILE.read_text(encoding="utf-8"))
    if Path(data.get("project_root", "")).resolve() != PROJECT_ROOT:
        raise RuntimeError("project_root в задании не совпадает с PROJECT_DIR службы")
    staging = Path(data.get("staging_path", "")).resolve()
    if not staging.is_dir() or PROJECT_ROOT / "updates" not in staging.parents:
        raise RuntimeError("Некорректная staging-папка")
    return data


def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "1.4"
    except FileNotFoundError:
        return "1.4"


def next_version(version: str, major_update: bool) -> str:
    try:
        major, minor = [int(part) for part in version.split(".", 1)]
    except Exception:
        major, minor = 1, 4
    if major_update:
        return f"{major + 1}.0"
    return f"{major}.{minor + 1}"


def copy_project_backup(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for item in PROJECT_ROOT.iterdir():
        if item.name in PROTECTED_TOP_LEVEL:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif item.is_file():
            shutil.copy2(item, target)
    db = PROJECT_ROOT / "bot.db"
    if db.exists():
        shutil.copy2(db, destination / "bot.db.backup")


def cleanup_old_backups() -> None:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted((p for p in BACKUPS_DIR.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[KEEP_BACKUPS:]:
        shutil.rmtree(old, ignore_errors=True)


def stop_bot() -> None:
    run(["systemctl", "stop", BOT_SERVICE])


def start_bot() -> None:
    run(["systemctl", "start", BOT_SERVICE])


def restore_backup(backup_code: Path) -> None:
    for item in list(PROJECT_ROOT.iterdir()):
        if item.name in PROTECTED_TOP_LEVEL:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in backup_code.iterdir():
        if item.name == "bot.db.backup":
            continue
        target = PROJECT_ROOT / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    db_backup = backup_code / "bot.db.backup"
    if db_backup.exists():
        shutil.copy2(db_backup, PROJECT_ROOT / "bot.db")


def apply_files(job: dict) -> tuple[int, int, int]:
    staging = Path(job["staging_path"])
    changed = 0
    new = 0
    deleted = 0
    for rel in job.get("files", []):
        source = staging / rel
        destination = PROJECT_ROOT / rel
        existed = destination.exists()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".update_tmp")
        shutil.copy2(source, temp)
        os.replace(temp, destination)
        if existed:
            changed += 1
        else:
            new += 1
    for rel in job.get("delete", []):
        target = PROJECT_ROOT / rel
        if target.is_dir():
            shutil.rmtree(target)
            deleted += 1
        elif target.exists():
            target.unlink()
            deleted += 1
    return changed, new, deleted


def install_requirements_if_needed(job: dict) -> None:
    if "requirements.txt" not in job.get("files", []):
        return
    python = PROJECT_ROOT / "venv" / "bin" / "python"
    if not python.exists():
        python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError("Не найден Python виртуального окружения")
    result = run([str(python), "-m", "pip", "install", "-r", str(PROJECT_ROOT / "requirements.txt")], check=False)
    if result.returncode != 0:
        raise RuntimeError("pip install завершился ошибкой: " + (result.stderr or result.stdout)[-3000:])


def validate_project() -> None:
    if not (PROJECT_ROOT / "main.py").is_file() or not (PROJECT_ROOT / "app").is_dir():
        raise RuntimeError("После обновления отсутствуют main.py или app")
    if not compileall.compile_dir(PROJECT_ROOT / "app", quiet=1, force=True):
        raise RuntimeError("Синтаксическая проверка папки app завершилась ошибкой")
    if not compileall.compile_file(PROJECT_ROOT / "main.py", quiet=1, force=True):
        raise RuntimeError("Синтаксическая проверка main.py завершилась ошибкой")


def wait_ready(started_after: float) -> dict:
    deadline = time.time() + START_TIMEOUT
    last_status = ""
    while time.time() < deadline:
        status = run(["systemctl", "is-active", BOT_SERVICE], check=False)
        last_status = (status.stdout or status.stderr).strip()
        if status.returncode == 0 and READY_FILE.exists():
            try:
                if READY_FILE.stat().st_mtime >= started_after:
                    return json.loads(READY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        time.sleep(2)
    journal = run(["journalctl", "-u", BOT_SERVICE, "-n", "40", "--no-pager"], check=False)
    raise RuntimeError(
        f"Бот не подтвердил готовность за {START_TIMEOUT} секунд; systemd={last_status}. "
        + (journal.stdout or journal.stderr)[-5000:]
    )


def write_result(job: dict, *, status: str, version: str, backup_name: str, error: str | None = None,
                 changed: int = 0, new: int = 0, deleted: int = 0) -> None:
    atomic_json(RESULT_FILE, {
        "status": status,
        "version": version,
        "admin_id": int(job.get("requested_by", 0)),
        "release_notes": job.get("release_notes", []),
        "backup_name": backup_name,
        "error": error,
        "changed_count": changed,
        "new_count": new,
        "deleted_count": deleted,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    })


def main() -> int:
    job = read_job()
    old_version = current_version()
    new_version = next_version(old_version, bool(job.get("major_update", False)))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUPS_DIR / f"v{old_version}_{timestamp}"
    backup_name = backup.name
    changed = new = deleted = 0

    log(f"Начинается обновление {old_version} -> {new_version}")

    try:
        # Сначала останавливаем процесс, чтобы копия SQLite была согласованной.
        stop_bot()
        copy_project_backup(backup)
        cleanup_old_backups()
        READY_FILE.unlink(missing_ok=True)
        changed, new, deleted = apply_files(job)
        VERSION_FILE.write_text(new_version + "\n", encoding="utf-8")
        install_requirements_if_needed(job)
        validate_project()
        started_after = time.time()
        start_bot()
        ready = wait_ready(started_after)
        if str(ready.get("version")) != new_version:
            raise RuntimeError(f"Бот запустился с неожиданной версией {ready.get('version')}")
        write_result(job, status="success", version=new_version, backup_name=backup_name,
                     changed=changed, new=new, deleted=deleted)
        log("Обновление успешно завершено")
        return 0
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log("Ошибка обновления: " + error)
        try:
            run(["systemctl", "stop", BOT_SERVICE], check=False)
            if backup.exists():
                restore_backup(backup)
            READY_FILE.unlink(missing_ok=True)
            started_after = time.time()
            start_bot()
            wait_ready(started_after)
            write_result(job, status="rollback", version=old_version, backup_name=backup_name, error=error)
            log("Откат выполнен успешно")
        except Exception as rollback_exc:
            catastrophic = error + f"; ОШИБКА ОТКАТА: {type(rollback_exc).__name__}: {rollback_exc}"
            write_result(job, status="rollback_failed", version=old_version, backup_name=backup_name, error=catastrophic)
            log(catastrophic)
        return 1
    finally:
        JOB_FILE.unlink(missing_ok=True)
        staging = Path(job.get("staging_path", ""))
        if staging.exists() and UPDATES_DIR in staging.parents:
            shutil.rmtree(staging, ignore_errors=True)
        archive = Path(job.get("archive_path", ""))
        if archive.exists() and UPDATES_DIR in archive.parents:
            archive.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
