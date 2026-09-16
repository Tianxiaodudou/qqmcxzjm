"""状态持久化：凭证、设置、历史记录（文件权限 600）。"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import env, security

_LOCK = threading.RLock()
MAX_HISTORY = 1000


def _read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


# --------------------------------------------------------------------------
# 凭证
# --------------------------------------------------------------------------
_credential: dict[str, Any] | None = None
_credential_loaded = False


def load_credentials() -> dict[str, Any] | None:
    """从 $TRIM_PKGVAR 读取凭证（仅后端可见）。"""
    global _credential, _credential_loaded
    with _LOCK:
        if not _credential_loaded:
            data = _read_json(env.CREDENTIAL_FILE, None)
            _credential = data if isinstance(data, dict) and data.get("musickey") else None
            _credential_loaded = True
        return _credential


def save_credentials(credential: dict[str, Any]) -> None:
    """保存凭证到磁盘，权限 600。"""
    global _credential, _credential_loaded
    with _LOCK:
        data = dict(credential)
        data["updated_at"] = int(time.time())
        _credential = data
        _credential_loaded = True
        _write_json(env.CREDENTIAL_FILE, data, 0o600)


def clear_credentials() -> None:
    """清空凭证（登出或登录过期）。"""
    global _credential, _credential_loaded
    with _LOCK:
        _credential = None
        _credential_loaded = True
        try:
            if env.CREDENTIAL_FILE.exists():
                env.CREDENTIAL_FILE.unlink()
        except OSError:
            pass


def is_logged_in() -> bool:
    cred = load_credentials()
    return bool(cred and cred.get("musickey") and cred.get("musicid"))


def login_status() -> dict[str, Any]:
    """返回给前端的登录态，绝不包含凭证本体。"""
    cred = load_credentials()
    if not cred:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "musicid": security.mask(str(cred.get("musicid", ""))),
        "nickname": cred.get("nickname") or "",
        "login_type": cred.get("login_type") or "",
        "updated_at": cred.get("updated_at") or 0,
    }


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------
DEFAULT_SETTINGS: dict[str, Any] = {
    "lyric_trans": env.DEFAULT_LYRIC_TRANS,
    "download_dir": env.WIZARD_MEDIA_DIR or str(env.DATA_DIR / "downloads"),
    "interval_min_ms": env.DEFAULT_INTERVAL_MIN_MS,
    "interval_max_ms": env.DEFAULT_INTERVAL_MAX_MS,
}


def load_settings() -> dict[str, Any]:
    with _LOCK:
        data = _read_json(env.SETTINGS_FILE, {})
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(data, dict):
            merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        if not merged.get("download_dir"):
            merged["download_dir"] = DEFAULT_SETTINGS["download_dir"]
        return merged


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        current = load_settings()
        current.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
        _write_json(env.SETTINGS_FILE, current, 0o600)
        return current


# --------------------------------------------------------------------------
# 下载历史
# --------------------------------------------------------------------------
def load_history() -> list[dict[str, Any]]:
    with _LOCK:
        data = _read_json(env.HISTORY_FILE, [])
        return data if isinstance(data, list) else []


def append_history(item: dict[str, Any]) -> None:
    """追加下载记录（只记录，不提供任何操作）。"""
    with _LOCK:
        items = load_history()
        record = dict(item)
        record.setdefault("id", f"{int(time.time() * 1000)}-{len(items)}")
        record.setdefault("time", int(time.time()))
        items.insert(0, record)
        if len(items) > MAX_HISTORY:
            del items[MAX_HISTORY:]
        _write_json(env.HISTORY_FILE, items, 0o600)


def clear_history() -> None:
    """清空历史记录：先把现有记录备份到 history.bak.json，避免误清无法找回。"""
    with _LOCK:
        current = _read_json(env.HISTORY_FILE, [])
        if isinstance(current, list) and current:
            _write_json(env.HISTORY_FILE.with_suffix(".json.bak"), current, 0o600)
        _write_json(env.HISTORY_FILE, [], 0o600)


def find_history(songmid: str) -> dict[str, Any] | None:
    for item in load_history():
        if item.get("songmid") == songmid:
            return item
    return None
