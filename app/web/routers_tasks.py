"""任务队列 / 历史记录 / 设置 相关 API。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, Field

from . import downloader, env, errors, fnos_api, notify, security, store
from .context import manager

logger = logging.getLogger("qqmusic.api.tasks")
router = APIRouter(tags=["tasks"])

MAX_BATCH = 100
FORBIDDEN_DIRS = (
    "/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc", "/dev",
    "/var/lib", "/var/run", "c:\\windows", "c:\\program files",
)


class SongRef(BaseModel):
    songmid: str = ""
    songid: int = 0
    name: str = ""
    singer: str = ""
    album_pmid: str = ""


class BatchRequest(BaseModel):
    songs: list[SongRef] = Field(default_factory=list)


class SettingsRequest(BaseModel):
    download_dir: str | None = None
    # 目录来自飞牛原生文件夹选择器（选择即授权），此时放行"授权记录尚未可查询"的短暂窗口
    dir_from_picker: bool = False
    lyric_trans: bool | None = None
    meta_full: bool | None = None
    meta_json: bool | None = None
    interval_min_ms: int | None = None
    interval_max_ms: int | None = None
    select_max: int | None = None
    # 首页四块推荐各自的数量上限（下限交给 QQ 服务器：拿不到就少显示）
    home_songlists_max: int | None = None
    home_newsongs_max: int | None = None
    home_guess_max: int | None = None
    home_radar_max: int | None = None
    # 消息推送（飞牛统一推送服务：POST {base}/api/push，Bearer token）
    push_base: str | None = None
    push_token: str | None = None
    push_on_success: bool | None = None
    push_on_fail: bool | None = None
    push_on_dup: bool | None = None
    push_on_expire: bool | None = None
    # 同类推送事件的去重窗口（分钟，0 = 不去重）
    push_dedup_minutes: int | None = None
    # 列表里的付费内容（无可用音源）怎么处理：gray 置灰不可选 / hide 直接隐藏
    paid_mode: str | None = None
    # 界面主题：light / dark / auto（跟随系统）
    theme: str | None = None


def _validate_dir(raw: str, extra_allowed: list[str] | None = None) -> Path:
    """下载目录必须为绝对路径、不能落在系统目录，且在授权范围内。"""
    text = (raw or "").strip()
    if not text:
        raise errors.BadRequestError("目录不能为空")
    if ".." in text:
        raise errors.BadRequestError("目录不能包含 ..")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise errors.BadRequestError("请填写绝对路径")
    resolved = str(path.resolve(strict=False))
    lowered = resolved.lower()
    if any(lowered == bad or lowered.startswith(bad + "/") or lowered.startswith(bad + "\\") for bad in FORBIDDEN_DIRS):
        raise errors.BadRequestError("该目录为系统目录，请更换")
    candidates = [p for p in (extra_allowed or []) if p]
    if not candidates:
        candidates = [p for p in env.AUTHORIZED_PATHS if p]
    if candidates and not security.is_authorized_dir(path, candidates):
        raise errors.BadRequestError("该目录未获得授权，请在「下载目录」中重新点选文件夹")
    return path


# --------------------------------------------------------------------------
# 飞牛目录授权：下载目录与文件夹访问权限已合并为一处点选
# --------------------------------------------------------------------------
def _requester_uid(request: Request) -> str:
    """当前用户 UID：只信任网关注入的请求头，不信任前端传值。"""
    for key in ("x-trim-userid", "x-trim-uid", "x-trim-username"):
        value = (request.headers.get(key) or "").strip()
        if value:
            return value
    return ""


async def _authorized_dirs(request: Request) -> dict[str, Any]:
    """查询当前用户已授权目录（个人授权 + 共享授权 + 启动注入）。

    ``ok`` 为 False 表示网关不可用或查询失败，调用方此时不应据此拒绝操作。
    """
    uid = _requester_uid(request)
    dirs: list[str] = []
    ok = False
    hint = ""
    if not fnos_api.available():
        hint = "未检测到飞牛应用网关，跳过授权目录校验"
    else:
        user_dirs = await fnos_api.user_authorized_dirs(uid)
        shared_dirs = await fnos_api.shared_authorized_dirs()
        ok = user_dirs is not None or shared_dirs is not None
        for item in list(user_dirs or []) + list(shared_dirs or []):
            if item and item not in dirs:
                dirs.append(item)
        if not ok:
            hint = "授权目录查询失败，已跳过校验"
        elif not dirs:
            hint = "尚未授权任何文件夹，请点击「选择文件夹…」"
    for item in env.AUTHORIZED_PATHS:
        if item and item not in dirs:
            dirs.append(item)
    return {"uid": uid, "ok": ok, "dirs": dirs, "hint": hint}


# --------------------------------------------------------------------------
# 任务队列
# --------------------------------------------------------------------------
@router.get("/tasks")
async def list_tasks() -> dict[str, Any]:
    tasks = manager.list_tasks()
    counts = {"running": 0, "paused": 0, "success": 0, "failed": 0}
    for task in tasks:
        if task["status"] == "success":
            counts["success"] += 1
        elif task["status"] == "failed":
            counts["failed"] += 1
        elif task["status"] == "paused":
            counts["paused"] += 1
        else:
            counts["running"] += 1
    # active = 真正在跑的任务数（暂停的不算），前端据此决定「全部暂停」是否可用
    return {"ok": True, "tasks": tasks, "counts": counts, "active": counts["running"]}


@router.post("/tasks")
async def create_tasks(payload: BatchRequest) -> dict[str, Any]:
    songs = [s.model_dump() for s in payload.songs if (s.songmid or s.songid)]
    if not songs:
        raise errors.BadRequestError("请先选择歌曲")
    if len(songs) > MAX_BATCH:
        raise errors.BadRequestError(f"单次最多创建 {MAX_BATCH} 个任务")
    # 去重：下载目录里已有同名成品就不再下载，直接提示「已有该音乐文件」
    target_dir = downloader.default_target_dir()
    existing = manager.find_existing_outputs(songs, target_dir)
    fresh: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for song in songs:
        songmid = str(song.get("songmid") or song.get("songid") or "")
        hit = existing.get(songmid)
        if hit:
            skipped.append(
                {"songmid": songmid, "name": str(song.get("name") or songmid), "path": hit}
            )
            notify.notify_duplicate(
                str(song.get("name") or songmid),
                f"下载目录已有同名文件，已跳过：{hit}",
                songmid,
            )
        else:
            fresh.append(song)
    # 音质无需指定：下载时自动选用登录账号可用的最高音质
    created = manager.create_batch(fresh)
    return {
        "ok": True,
        "created": [t.to_public() for t in created],
        "skipped": skipped,
        "download_dir": str(target_dir),
    }


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str) -> dict[str, Any]:
    task = manager.retry(task_id)
    return {"ok": True, "task": task.to_public()}


@router.post("/tasks/pause")
async def pause_tasks() -> dict[str, Any]:
    """全部暂停：正在下载的任务在下一个数据块处停下，已下的部分留作续传用。"""
    changed = manager.pause_all()
    return {
        "ok": True,
        "paused": changed,
        "active": manager.active_count(),
        "tasks": manager.list_tasks(),
        "message": f"已暂停 {changed} 个任务" if changed else "没有正在下载的任务",
    }


@router.post("/tasks/resume")
async def resume_tasks() -> dict[str, Any]:
    """全部继续：暂停的任务重新排队，已下载的部分会接着下（断点续传）。"""
    changed = manager.resume_all()
    return {
        "ok": True,
        "resumed": changed,
        "tasks": manager.list_tasks(),
        "message": f"已继续 {changed} 个任务" if changed else "没有暂停中的任务",
    }


@router.post("/tasks/retry_all")
async def retry_all_tasks() -> dict[str, Any]:
    """全部重试：失败与暂停的任务一起重来。"""
    changed = manager.retry_all()
    return {
        "ok": True,
        "retried": changed,
        "tasks": manager.list_tasks(),
        "message": f"已重新加入 {changed} 个任务" if changed else "没有失败或暂停的任务",
    }


@router.post("/tasks/clear")
async def clear_tasks(scope: str = Query("finished")) -> dict[str, Any]:
    """scope=finished 只清除已完成记录；scope=all 清除全部（运行中的任务会保留）。"""
    if scope not in ("finished", "all"):
        raise errors.BadRequestError("不支持的清理范围")
    removed = manager.clear_finished(include_failed=(scope == "all"))
    kept = sum(1 for t in manager.list_tasks() if t.get("status") == "downloading")
    return {"ok": True, "removed": removed, "kept": kept}


# --------------------------------------------------------------------------
# 历史记录（含文件存在性检查）
# --------------------------------------------------------------------------
_check_cache: dict[str, tuple[str, dict[str, str]]] = {}
_check_lock = threading.Lock()


def _file_state(raw: str) -> str:
    if not raw:
        return "missing"
    try:
        return "ok" if Path(raw).is_file() else "missing"
    except OSError:
        return "missing"


@router.get("/history")
async def list_history(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    items = store.load_history()[:limit]
    return {"ok": True, "items": items, "total": len(store.load_history())}


@router.post("/history/check")
async def check_history(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """只对前端传入的可见条目做检查（避免全量扫描）。"""
    songmids = [str(s) for s in (payload.get("songmids") or [])][:300]
    index = {str(i.get("songmid")): i for i in store.load_history()}
    result: dict[str, dict[str, str]] = {}
    for songmid in songmids:
        item = index.get(songmid)
        if not item:
            result[songmid] = {"output": "unknown", "fingerprint": "none"}
            continue
        fingerprint = security.file_fingerprint(Path(item.get("output") or ""))
        with _check_lock:
            cached = _check_cache.get(songmid)
        if cached and cached[0] == fingerprint:
            result[songmid] = cached[1]
            continue
        state = {
            "output": _file_state(item.get("output") or ""),
            "fingerprint": fingerprint,
        }
        with _check_lock:
            _check_cache[songmid] = (fingerprint, state)
        result[songmid] = state
    return {"ok": True, "items": result}


@router.post("/history/clear")
async def clear_history() -> dict[str, Any]:
    store.clear_history()
    with _check_lock:
        _check_cache.clear()
    return {"ok": True}


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------
@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    settings = store.load_settings()
    auth = await _authorized_dirs(request)
    return {
        "ok": True,
        "settings": settings,
        "authorized_dirs": auth["dirs"],
        # 网关不可用时按"无授权信息"处理，避免开发/独立环境下锁死设置
        "authorized_ok": bool(auth["ok"]) or not fnos_api.available(),
        "authorized_hint": auth["hint"],
    }


@router.post("/settings")
async def update_settings(payload: SettingsRequest, request: Request) -> dict[str, Any]:
    settings = store.load_settings()
    if payload.download_dir is not None:
        auth = await _authorized_dirs(request)
        extra = list(auth["dirs"])
        if payload.dir_from_picker:
            # 原生选择器返回值 = 用户当场授权；同时用 ACL 接口做一次独立复核
            if auth["ok"] and auth["uid"]:
                allowed = await fnos_api.check_user_acl(auth["uid"], payload.download_dir)
                if allowed is False:
                    raise errors.BadRequestError("该目录未获得授权，请重新选择")
            extra.append(payload.download_dir)
        settings["download_dir"] = str(_validate_dir(payload.download_dir, extra))
    if payload.lyric_trans is not None:
        settings["lyric_trans"] = bool(payload.lyric_trans)
    if payload.meta_full is not None:
        settings["meta_full"] = bool(payload.meta_full)
    if payload.meta_json is not None:
        settings["meta_json"] = bool(payload.meta_json)
    if payload.interval_min_ms is not None:
        settings["interval_min_ms"] = max(100, min(5000, int(payload.interval_min_ms)))
    if payload.interval_max_ms is not None:
        settings["interval_max_ms"] = max(100, min(8000, int(payload.interval_max_ms)))
    if settings["interval_max_ms"] < settings["interval_min_ms"]:
        settings["interval_max_ms"] = settings["interval_min_ms"]
    if payload.select_max is not None:
        # 「全选」单次上限：最少 10 首，最多 20000 首（防手滑填出天量翻页）
        settings["select_max"] = max(10, min(20000, int(payload.select_max)))
    for key, low, high in (
        ("home_songlists_max", 1, 60),     # 推荐歌单：最多显示几个（QQ 单页上限 30，可翻页）
        ("home_newsongs_max", 1, 100),     # 新歌推荐：最多显示几首
        ("home_guess_max", 1, 60),         # 猜你喜欢：最多显示几首
        ("home_radar_max", 1, 100),        # 每日推荐（私人雷达）：最多显示几首
    ):
        value = getattr(payload, key)
        if value is not None:
            settings[key] = max(low, min(high, int(value)))
    if payload.push_base is not None:
        base = payload.push_base.strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            raise errors.BadRequestError("推送服务地址需以 http:// 或 https:// 开头")
        settings["push_base"] = base
    if payload.push_token is not None:
        settings["push_token"] = payload.push_token.strip()
    for key in ("push_on_success", "push_on_fail", "push_on_dup", "push_on_expire"):
        value = getattr(payload, key)
        if value is not None:
            settings[key] = bool(value)
    if payload.push_dedup_minutes is not None:
        # 0 表示不去重；上限 24 小时，避免手滑填出天文数字把推送全吞掉
        settings["push_dedup_minutes"] = max(0, min(24 * 60, int(payload.push_dedup_minutes)))
    if payload.paid_mode is not None:
        mode = str(payload.paid_mode).strip().lower()
        if mode not in store.VALID_PAID_MODES:
            raise errors.BadRequestError("付费内容处理方式只能是 gray / hide")
        settings["paid_mode"] = mode
    if payload.theme is not None:
        theme = str(payload.theme).strip().lower()
        if theme not in store.VALID_THEMES:
            raise errors.BadRequestError("主题只能是 light / dark / auto")
        settings["theme"] = theme
    store.save_settings(settings)
    return {"ok": True, "settings": settings}


# --------------------------------------------------------------------------
# 消息日志 / 推送记录 / 测试通知
# --------------------------------------------------------------------------
LEVELS = ("success", "error", "warn", "info")


@router.get("/logs")
async def list_logs(limit: int = Query(200, ge=1, le=500), level: str = Query("")) -> dict[str, Any]:
    """消息日志：下载成功/失败、已有同名文件、登录态过期等事件都会记一条。"""
    items = store.load_logs(limit)
    if level in LEVELS:
        items = [item for item in items if item.get("level") == level]
    counts = {name: 0 for name in LEVELS}
    for item in store.load_logs(store.MAX_LOGS):
        key = str(item.get("level") or "info")
        counts[key] = counts.get(key, 0) + 1
    return {"ok": True, "items": items, "counts": counts}


@router.post("/logs/clear")
async def clear_logs() -> dict[str, Any]:
    """清空消息日志。"""
    store.clear_logs()
    return {"ok": True}


@router.get("/push/records")
async def push_records(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """推送记录：每次实际调用推送服务的结果（成功/失败/状态码）。"""
    return {"ok": True, "items": store.load_push_records(limit)}


@router.post("/push/records/clear")
async def clear_push_records() -> dict[str, Any]:
    """清空推送记录。"""
    store.clear_push_records()
    return {"ok": True}


@router.post("/push/test")
async def push_test() -> dict[str, Any]:
    """测试通知：按当前设置推一条测试消息（同时记日志与推送记录）。"""
    return {"ok": True, "result": notify.send_test()}
