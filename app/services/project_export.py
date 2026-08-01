from __future__ import annotations

import hashlib
import os
import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from app.version import get_version

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ROOT_FILES = {
    "main.py",
    "requirements.txt",
    "VERSION",
    "update_manifest.example.json",
    "ОБНОВЛЕНИЕ_ПРОЕКТА.md",
    "УСТАНОВКА_АВТООБНОВЛЕНИЯ.md",
    ".gitignore",
}

DEPLOY_FILES = {
    "batyabot2-updater-sudoers",
    "batyabot2-updater.service",
    "batyabot2.service",
    "batyabot_updater.py",
}

FORBIDDEN_NAMES = {
    ".env",
    "bot.db",
    "venv",
    ".venv",
    "backups",
    "deploy_backups",
    "logs",
    "updates",
    ".git",
    "__pycache__",
    "snap",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log"}


def _allowed_file(path: Path) -> bool:
    if path.is_symlink():
        return False

    try:
        relative = path.relative_to(PROJECT_ROOT)
        path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return False

    if any(part in FORBIDDEN_NAMES for part in relative.parts):
        return False
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return False
    if len(relative.parts) == 1:
        return relative.name in ROOT_FILES
    if relative.parts[0] == "app":
        return path.suffix.lower() == ".py"
    if relative.parts[0] == "deploy":
        return len(relative.parts) == 2 and relative.name in DEPLOY_FILES
    if relative.parts[:2] == ("runtime", "interface_versions"):
        return len(relative.parts) == 3 and path.suffix.lower() == ".json"
    return False


def list_export_files() -> list[Path]:
    return sorted(
        (path for path in PROJECT_ROOT.rglob("*") if path.is_file() and _allowed_file(path)),
        key=lambda item: item.relative_to(PROJECT_ROOT).as_posix(),
    )


def create_project_export() -> tuple[Path, int, str]:
    files = list_export_files()
    version = get_version()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    handle, temp_name = tempfile.mkstemp(prefix=f"BatyaBot2_v{version}_source_", suffix=".zip")
    os.close(handle)
    Path(temp_name).unlink(missing_ok=True)
    export_path = Path(temp_name)

    file_names = [path.relative_to(PROJECT_ROOT).as_posix() for path in files]
    info = {
        "project": "BatyaBot2",
        "version": version,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "Архив исходного кода для анализа и подготовки обновлений",
        "excluded": [
            ".env и токены",
            "рабочие базы данных",
            "резервные копии",
            "логи",
            "виртуальное окружение",
            "временные файлы обновлятора",
            "Git-метаданные",
        ],
        "files": file_names,
    }

    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("EXPORT_INFO.json", json.dumps(info, ensure_ascii=False, indent=2))
        for source in files:
            archive.write(source, source.relative_to(PROJECT_ROOT).as_posix())

    digest = hashlib.sha256(export_path.read_bytes()).hexdigest()
    return export_path, len(files), digest
