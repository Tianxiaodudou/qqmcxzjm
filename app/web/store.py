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
        _remember_on_save(data)  # 同步进账号列表（多账号切换用）


def clear_credentials(remove_account: bool = False) -> None:
    """清空当前凭证（登录过期 / 退出登录）。

    remove_account=False：只清空登录态，账号条目仍留在账号列表里（登录过期场景）；
    remove_account=True：退出登录，把该账号从账号列表里彻底移除。
    """
    global _credential, _credential_loaded
    with _LOCK:
        current = _credential or load_credentials()
        key = account_key(current)
        _credential = None
        _credential_loaded = True
        try:
            if env.CREDENTIAL_FILE.exists():
                env.CREDENTIAL_FILE.unlink()
        except OSError:
            pass
        if key:
            if remove_account:
                forget_account(key)
            else:
                data = load_accounts()
                if data.get("current") == key:
                    save_accounts({"current": "", "accounts": data["accounts"]})


def is_logged_in() -> bool:
    cred = load_credentials()
    return bool(cred and cred.get("musickey") and cred.get("musicid"))


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------
VALID_PAID_MODES = ("gray", "hide")
VALID_THEMES = ("light", "dark", "auto")
# 推送消息类型：纯文本 / Markdown / HTML
VALID_PUSH_TYPES = ("text", "markdown", "html")
# 首页各板块的显示上限（1~100）：从设置文件读到的值统一钳到这个区间，越界/脏数据一律归位
LIMIT_SETTING_KEYS = (
    "home_songlists_max",
    "home_newsongs_max",
    "home_guess_max",
    "home_radar_max",
    "home_feed_max",
    "home_chart_max",
    "home_newalbum_max",
    "home_singer_max",
    "home_hotkey_max",
    "home_daily_max",
    "home_similar_max",
    "home_fav_max",
)


def _coerce_int(value: Any, default: int) -> int:
    """尽量转成整数，失败返回默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_limit(value: Any, default: int) -> int:
    """板块显示上限：1~100 之外的脏数据钳回默认值。"""
    number = _coerce_int(value, default)
    if 1 <= number <= 100:
        return number
    return int(default)


# 每个推送事件各自的「数量阈值」设置键：累计多少条明细才推送一次（1 = 每条即时推送）
BATCH_COUNT_SETTING_KEYS = (
    "push_batch_success_count",     # 下载成功
    "push_batch_fail_count",        # 下载失败
    "push_batch_expire_count",      # 登录态过期
)

# 旧版「同类事件去重分钟数」设置键：已废弃，读取时直接忽略（保存设置时自然被清掉）
LEGACY_DEDUP_SETTING_KEYS = (
    "push_dedup_success_minutes",
    "push_dedup_dup_minutes",
    "push_dedup_fail_minutes",
    "push_dedup_expire_minutes",
)


def clean_dir_list(value: Any, limit: int = 50) -> list[str]:
    """把目录名单归一化：只留非空字符串、去重、截断到 limit 条。"""
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items

DEFAULT_SETTINGS: dict[str, Any] = {
    "lyric_trans": env.DEFAULT_LYRIC_TRANS,
    "download_dir": env.WIZARD_MEDIA_DIR or str(env.DATA_DIR / "downloads"),
    # 下载目录候选里被用户「移除」的目录（应用内隐藏名单；不动飞牛侧的授权记录）
    "dir_hidden": [],
    # 列表里的付费内容（无可用音源）怎么显示：gray=置灰且不可选中，hide=直接隐藏
    "paid_mode": "gray",
    # 界面主题：light / dark / auto（跟随系统）
    "theme": "auto",
    # 推送消息类型：text / markdown / html
    "push_type": "text",
    # 同类事件「数量阈值」：累计多少条明细推送一次（1 = 每条即时推送，汇总成一条）
    "push_batch_success_count": 1,
    "push_batch_fail_count": 1,
    "push_batch_expire_count": 1,
    "interval_min_ms": env.DEFAULT_INTERVAL_MIN_MS,
    "interval_max_ms": env.DEFAULT_INTERVAL_MAX_MS,
    "meta_full": env.DEFAULT_META_FULL,
    "meta_json": env.DEFAULT_META_JSON,
    "select_max": env.DEFAULT_SELECT_MAX,
    "home_songlists_max": env.DEFAULT_HOME_SONGLISTS_MAX,
    "home_newsongs_max": env.DEFAULT_HOME_NEWSONGS_MAX,
    "home_guess_max": env.DEFAULT_HOME_GUESS_MAX,
    "home_radar_max": env.DEFAULT_HOME_RADAR_MAX,
    # 首页新增板块：每块的显示上限（1~100）与是否显示（可隐藏）
    "home_feed_max": env.DEFAULT_HOME_FEED_MAX,
    "home_chart_max": env.DEFAULT_HOME_CHART_MAX,
    "home_newalbum_max": env.DEFAULT_HOME_NEWALBUM_MAX,
    "home_singer_max": env.DEFAULT_HOME_BLOCK_MAX,
    "home_hotkey_max": env.DEFAULT_HOME_HOTKEY_MAX,
    "home_daily_max": env.DEFAULT_HOME_DAILY_MAX,
    "home_similar_max": env.DEFAULT_HOME_SIMILAR_MAX,
    "home_fav_max": env.DEFAULT_HOME_FAV_MAX,
    "home_feed_show": True,
    "home_chart_show": True,
    "home_newalbum_show": True,
    "home_singer_show": True,
    "home_hotkey_show": True,
    "home_daily_show": True,
    "home_similar_show": True,
    "home_fav_show": True,
    # 歌曲卡片增强：收藏数 / 评论数 / 榜单标签（进列表才拉，故默认开）
    "song_stats_show": True,
    # 「每日30首」用的歌单 ID：0=自动挑一个公开日推歌单
    "daily_songlist_id": env.DEFAULT_DAILY_SONGLIST_ID,
    "push_base": env.DEFAULT_PUSH_BASE,
    "push_token": env.DEFAULT_PUSH_TOKEN,
    "push_on_success": True,
    "push_on_dup": True,
    "push_on_fail": True,
    "push_on_expire": True,
}


def load_settings() -> dict[str, Any]:
    with _LOCK:
        data = _read_json(env.SETTINGS_FILE, {})
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(data, dict):
            merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        if not merged.get("download_dir"):
            merged["download_dir"] = DEFAULT_SETTINGS["download_dir"]
        # 旧版本允许的 paid_mode=off 已取消（需求为「置灰 / 隐藏」二选一），历史配置归一为默认值
        if merged.get("paid_mode") not in VALID_PAID_MODES:
            merged["paid_mode"] = DEFAULT_SETTINGS["paid_mode"]
        if merged.get("theme") not in VALID_THEMES:
            merged["theme"] = DEFAULT_SETTINGS["theme"]
        # 各板块上限：非数字或越界一律钳到 1~100，避免前端拿着脏值去请求
        for key in LIMIT_SETTING_KEYS:
            value = _coerce_limit(merged.get(key), DEFAULT_SETTINGS[key])
            merged[key] = value
        merged["daily_songlist_id"] = max(0, _coerce_int(merged.get("daily_songlist_id"), 0))
        if merged.get("push_type") not in VALID_PUSH_TYPES:
            merged["push_type"] = DEFAULT_SETTINGS["push_type"]
        # 分事件数量阈值：1~1000 之外一律钳回（0/负值视为 1 = 每条即时推送）
        for key in BATCH_COUNT_SETTING_KEYS:
            merged[key] = max(1, min(1000, _coerce_int(merged.get(key), DEFAULT_SETTINGS[key])))
        # 目录隐藏名单：脏数据（非列表/空串/重复）一律清理
        merged["dir_hidden"] = clean_dir_list(merged.get("dir_hidden"))
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


# --------------------------------------------------------------------------
# 消息日志（下载成功 / 下载失败 / 已有同名文件 / 登录态过期 …）
# --------------------------------------------------------------------------
MAX_LOGS = 500
LOG_LEVELS = ("success", "error", "warn", "info")


def load_logs(limit: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        data = _read_json(env.LOGS_FILE, [])
        items = data if isinstance(data, list) else []
        return items[: max(int(limit), 0)] if limit else items


def append_log(level: str, event: str, title: str = "", detail: str = "") -> dict[str, Any]:
    """追加一条消息日志（内存 + 落盘，最多 MAX_LOGS 条）。"""
    with _LOCK:
        items = _read_json(env.LOGS_FILE, [])
        if not isinstance(items, list):
            items = []
        record = {
            "id": f"{int(time.time() * 1000)}-{len(items)}",
            "time": int(time.time()),
            "level": level if level in LOG_LEVELS else "info",
            "event": str(event or ""),
            "title": str(title or ""),
            "detail": str(detail or "")[:2000],
        }
        items.insert(0, record)
        del items[MAX_LOGS:]
        _write_json(env.LOGS_FILE, items, 0o600)
        return record


def clear_logs() -> None:
    with _LOCK:
        _write_json(env.LOGS_FILE, [], 0o600)


# --------------------------------------------------------------------------
# 推送记录（每次推送成功/失败都记一条）
# --------------------------------------------------------------------------
MAX_PUSH_RECORDS = 300


def load_push_records(limit: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        data = _read_json(env.PUSH_LOG_FILE, [])
        items = data if isinstance(data, list) else []
        return items[: max(int(limit), 0)] if limit else items


def append_push_record(entry: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        items = _read_json(env.PUSH_LOG_FILE, [])
        if not isinstance(items, list):
            items = []
        record = dict(entry)
        record.setdefault("time", int(time.time()))
        record.setdefault("id", f"{int(time.time() * 1000)}-{len(items)}")
        items.insert(0, record)
        del items[MAX_PUSH_RECORDS:]
        _write_json(env.PUSH_LOG_FILE, items, 0o600)
        return record


def clear_push_records() -> None:
    with _LOCK:
        _write_json(env.PUSH_LOG_FILE, [], 0o600)


def recent_push_time(dedup_key: str) -> int:
    """返回同一去重键最近一次**成功**推送的时间戳（0 表示没有）。"""
    for item in load_push_records(limit=120):
        if item.get("dedup_key") == dedup_key and item.get("ok"):
            return int(item.get("time") or 0)
    return 0


# --------------------------------------------------------------------------
# 多账号（每个账号各自保留登录态，可随时切换）
# --------------------------------------------------------------------------
def account_key(credential: dict[str, Any] | None) -> str:
    """账号唯一标识：优先 musicid，其次 str_musicid / encrypt_uin。"""
    if not isinstance(credential, dict):
        return ""
    for field in ("musicid", "str_musicid", "encrypt_uin", "openid"):
        value = credential.get(field)
        if value not in (None, "", 0, "0"):
            return str(value)
    return ""


def load_accounts() -> dict[str, Any]:
    with _LOCK:
        data = _read_json(env.ACCOUNTS_FILE, {})
        if not isinstance(data, dict):
            data = {}
        accounts = data.get("accounts")
        if not isinstance(accounts, list):
            accounts = []
        return {"current": str(data.get("current") or ""), "accounts": accounts}


def save_accounts(data: dict[str, Any]) -> None:
    with _LOCK:
        _write_json(env.ACCOUNTS_FILE, data, 0o600)


def _account_entry(credential: dict[str, Any]) -> dict[str, Any]:
    """账号摘要：不含完整凭证，凭证单独存放在 accounts.json 的 credential 字段。"""
    meta = dict(credential)
    return {
        "key": account_key(credential),
        "musicid": meta.get("musicid") or meta.get("str_musicid") or "",
        "musicid_mask": security.mask(str(meta.get("musicid") or meta.get("str_musicid") or "")),
        "nickname": meta.get("nickname") or "",
        "avatar": meta.get("avatar") or "",
        "vip_level": meta.get("vip_level") or "",
        "vip_desc": meta.get("vip_desc") or "",
        "vip_expire": meta.get("vip_expire") or "",
        "login_type": meta.get("login_type") or "",
        "updated_at": int(meta.get("updated_at") or time.time()),
    }


def remember_account(credential: dict[str, Any]) -> None:
    """把（刚登录/刷新过的）凭证登记进账号列表，并设为当前账号。"""
    key = account_key(credential)
    if not key:
        return
    with _LOCK:
        data = load_accounts()
        creds = dict(credential)
        creds.pop("nickname", None)
        creds.pop("avatar", None)
        # 昵称/头像等展示字段跟着凭证一起存，方便切换后立即显示
        meta = _account_entry(credential)
        entry = dict(meta)
        entry["credential"] = creds
        accounts = [a for a in data["accounts"] if a.get("key") != key]
        old = next((a for a in data["accounts"] if a.get("key") == key), None)
        if old:  # 保留旧的展示信息，避免刷新凭证时把昵称清空
            for field in ("nickname", "avatar", "vip_level", "vip_desc", "vip_expire"):
                if not entry.get(field) and old.get(field):
                    entry[field] = old[field]
        accounts.insert(0, entry)
        save_accounts({"current": key, "accounts": accounts})


def update_account_meta(key: str, **meta: Any) -> None:
    """更新账号展示信息（昵称 / 头像 / 会员等级…）。"""
    if not key:
        return
    with _LOCK:
        data = load_accounts()
        changed = False
        for item in data["accounts"]:
            if item.get("key") == key:
                for name, value in meta.items():
                    if value not in (None, ""):
                        item[name] = value
                        changed = True
        if changed:
            save_accounts(data)


def forget_account(key: str) -> None:
    """退出登录：从账号列表里彻底移除该账号。"""
    if not key:
        return
    with _LOCK:
        data = load_accounts()
        accounts = [a for a in data["accounts"] if a.get("key") != key]
        current = data.get("current") or ""
        if current == key:
            current = account_key(accounts[0].get("credential")) if accounts else ""
        save_accounts({"current": current, "accounts": accounts})


def account_list() -> list[dict[str, Any]]:
    """给前端展示的账号列表（剔除凭证本体）。"""
    data = load_accounts()
    current = data.get("current") or ""
    items = []
    for item in data["accounts"]:
        row = {k: v for k, v in item.items() if k != "credential"}
        row["current"] = item.get("key") == current
        items.append(row)
    return items


def switch_account(key: str) -> dict[str, Any] | None:
    """切换当前账号：把该账号的凭证写成「当前凭证」。"""
    with _LOCK:
        data = load_accounts()
        target = next((a for a in data["accounts"] if a.get("key") == str(key)), None)
        if not target or not isinstance(target.get("credential"), dict):
            return None
        credential = dict(target["credential"])
        credential["updated_at"] = int(time.time())
        save_accounts({"current": str(key), "accounts": data["accounts"]})
        globals()["_credential"] = credential
        globals()["_credential_loaded"] = True
        _write_json(env.CREDENTIAL_FILE, credential, 0o600)
        return credential


def _remember_on_save(credential: dict[str, Any]) -> None:
    """save_credentials 的钩子：把凭证同步进账号列表。"""
    try:
        remember_account(credential)
    except Exception:  # noqa: BLE001 账号登记失败不应阻断登录
        pass


def account_info_for(key: str) -> dict[str, Any]:
    """读取某账号的展示信息（昵称/头像/会员）。"""
    if not key:
        return {}
    for item in load_accounts()["accounts"]:
        if item.get("key") == key:
            return item
    return {}


def remove_account(key: str) -> dict[str, Any]:
    """从账号列表里删掉一个账号（仅列表，不影响其他账号）。"""
    data = load_accounts()
    key = str(key)
    data["accounts"] = [a for a in data["accounts"] if a.get("key") != key]
    if data.get("current") == key:
        data["current"] = data["accounts"][0]["key"] if data["accounts"] else ""
    save_accounts(data)
    return {"removed": key, "current": data.get("current", "")}
