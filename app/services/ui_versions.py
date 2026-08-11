from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERFACE_VERSIONS_DIR = PROJECT_ROOT / "runtime" / "interface_versions"
ACTIVE_INTERFACE_FILE = INTERFACE_VERSIONS_DIR / "active.json"
KEEP_INTERFACE_VERSIONS = 5

LEGACY_UI_ID = "ui_2_2_classic"
CURRENT_UI_ID = "ui_2_7_pc_workspace"

BUILTIN_PROFILES: tuple[dict, ...] = (
    {
        "id": LEGACY_UI_ID,
        "title": "Классический интерфейс 2.2",
        "app_version": "2.2",
        "created_at": "2026-07-30T00:00:00+03:00",
        "description": "Прежнее главное меню без центра помощи и только строгие системные сообщения.",
        "config": {
            "show_help_button": False,
            "show_help_settings": False,
            "allow_friendly_style": False,
            "compact_main_menu": False,
        },
    },
    {
        "id": "ui_2_3_help_center",
        "title": "Центр помощи 2.3",
        "app_version": "2.3",
        "created_at": "2026-07-30T16:30:00+03:00",
        "description": "Помощь, FAQ, свободные сообщения и выбор стиля системных фраз. Старое большое главное меню.",
        "config": {
            "show_help_button": True,
            "show_help_settings": True,
            "allow_friendly_style": True,
            "compact_main_menu": False,
        },
    },
    {
        "id": "ui_2_5_compact_tickets",
        "title": "Компактное меню 2.5",
        "app_version": "2.5",
        "created_at": "2026-08-01T19:30:00+03:00",
        "description": "Главное меню из трёх рядов и отдельный раздел «Работа с тикетами».",
        "config": {
            "show_help_button": True,
            "show_help_settings": True,
            "allow_friendly_style": True,
            "compact_main_menu": True,
        },
    },
    {
        "id": CURRENT_UI_ID,
        "title": "PC-first workspace 2.7",
        "app_version": "2.7.0",
        "created_at": "2026-08-11T19:47:00+03:00",
        "description": "Единая рабочая область тикетов для Telegram Desktop: списки и карточки редактируют одно сообщение, нижнее меню сохранено.",
        "config": {
            "show_help_button": True,
            "show_help_settings": True,
            "allow_friendly_style": True,
            "compact_main_menu": True,
            "pc_ticket_workspace": True,
        },
    },
)

DEFAULT_CONFIG = {
    "show_help_button": True,
    "show_help_settings": True,
    "allow_friendly_style": True,
    "compact_main_menu": True,
    "pc_ticket_workspace": False,
}


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _profile_path(version_id: str) -> Path:
    safe = "".join(char for char in str(version_id) if char.isalnum() or char in {"_", "-"})
    if not safe or safe != version_id:
        raise ValueError("Некорректный идентификатор версии интерфейса")
    return INTERFACE_VERSIONS_DIR / f"{safe}.json"


def ensure_ui_versions() -> None:
    INTERFACE_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    current_profile_was_missing = not _profile_path(CURRENT_UI_ID).exists()

    for profile in BUILTIN_PROFILES:
        path = _profile_path(profile["id"])
        if not path.exists():
            _atomic_json(path, profile)

    if current_profile_was_missing and ACTIVE_INTERFACE_FILE.exists():
        previous_active = get_active_ui_id(ensure=False)
        if previous_active in {"ui_2_3_help_center", "ui_2_5_compact_tickets"}:
            _atomic_json(ACTIVE_INTERFACE_FILE, {"active_id": CURRENT_UI_ID})

    profiles = list_ui_versions(ensure=False)
    active_before_cleanup = get_active_ui_id(ensure=False)
    keep_ids = [str(item.get("id")) for item in profiles[:KEEP_INTERFACE_VERSIONS]]
    if active_before_cleanup not in keep_ids and any(
        str(item.get("id")) == active_before_cleanup for item in profiles
    ):
        if keep_ids:
            keep_ids[-1] = active_before_cleanup
        else:
            keep_ids.append(active_before_cleanup)

    for old in profiles:
        old_id = str(old.get("id"))
        if old_id in keep_ids:
            continue
        try:
            _profile_path(old_id).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    if not ACTIVE_INTERFACE_FILE.exists():
        _atomic_json(ACTIVE_INTERFACE_FILE, {"active_id": CURRENT_UI_ID})
        return

    active_id = get_active_ui_id(ensure=False)
    if not any(item.get("id") == active_id for item in list_ui_versions(ensure=False)):
        _atomic_json(ACTIVE_INTERFACE_FILE, {"active_id": CURRENT_UI_ID})


def list_ui_versions(*, ensure: bool = True) -> list[dict]:
    if ensure:
        INTERFACE_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    profiles: list[dict] = []
    if not INTERFACE_VERSIONS_DIR.exists():
        return profiles

    for path in INTERFACE_VERSIONS_DIR.glob("ui_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("id"):
                profiles.append(payload)
        except (OSError, json.JSONDecodeError):
            continue

    profiles.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return profiles


def get_active_ui_id(*, ensure: bool = True) -> str:
    if ensure:
        ensure_ui_versions()
    try:
        payload = json.loads(ACTIVE_INTERFACE_FILE.read_text(encoding="utf-8"))
        value = str(payload.get("active_id", "")).strip()
        return value or CURRENT_UI_ID
    except (OSError, json.JSONDecodeError):
        return CURRENT_UI_ID


def get_ui_version(version_id: str) -> dict | None:
    ensure_ui_versions()
    try:
        payload = json.loads(_profile_path(version_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def get_active_ui_profile() -> dict:
    ensure_ui_versions()
    profile = get_ui_version(get_active_ui_id(ensure=False))
    if profile:
        return profile
    return {
        "id": CURRENT_UI_ID,
        "title": "Текущий интерфейс",
        "config": dict(DEFAULT_CONFIG),
    }


def get_active_ui_config() -> dict:
    profile = get_active_ui_profile()
    config = profile.get("config")
    result = dict(DEFAULT_CONFIG)
    if isinstance(config, dict):
        result.update(config)
    return result


def activate_ui_version(version_id: str) -> dict:
    profile = get_ui_version(version_id)
    if not profile:
        raise ValueError("Версия интерфейса не найдена")
    _atomic_json(ACTIVE_INTERFACE_FILE, {"active_id": version_id})
    return profile


def help_button_enabled() -> bool:
    return bool(get_active_ui_config().get("show_help_button", True))


def help_settings_enabled() -> bool:
    return bool(get_active_ui_config().get("show_help_settings", True))


def friendly_style_enabled() -> bool:
    return bool(get_active_ui_config().get("allow_friendly_style", True))


def compact_main_menu_enabled() -> bool:
    return bool(get_active_ui_config().get("compact_main_menu", True))


def pc_ticket_workspace_enabled() -> bool:
    return bool(get_active_ui_config().get("pc_ticket_workspace", False))
