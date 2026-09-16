"""任务队列 / 历史记录 / 设置 相关 API。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, Field

from . import env, errors, fnos_api, security, store
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
    interval_min_ms: int | None = None
    interval_max_ms: int | None = None


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
    counts = {"running": 0, "success": 0, "failed": 0}
    for task in tasks:
        if task["status"] == "success":
            counts["success"] += 1
        elif task["status"] == "failed":
            counts["failed"] += 1
        else:
            counts["running"] += 1
    return {"ok": True, "tasks": tasks, "counts": counts}


@router.post("/tasks")
async def create_tasks(payload: BatchRequest) -> dict[str, Any]:
    songs = [s.model_dump() for s in payload.songs if (s.songmid or s.songid)]
    if not songs:
        raise errors.BadRequestError("请先选择歌曲")
    if len(songs) > MAX_BATCH:
        raise errors.BadRequestError(f"单次最多创建 {MAX_BATCH} 个任务")
    # 音质无需指定：下载时自动选用登录账号可用的最高音质
    created = manager.create_batch(songs)
    return {"ok": True, "created": [t.to_public() for t in created]}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str) -> dict[str, Any]:
    task = manager.retry(task_id)
    return {"ok": True, "task": task.to_public()}


@router.post("/tasks/clear")
async def clear_tasks(scope: str = Query("finished")) -> dict[str, Any]:
    if scope not in ("finished",):
        raise errors.BadRequestError("不支持的清理范围")
    removed = manager.clear_finished()
    return {"ok": True, "removed": removed}


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
    if payload.interval_min_ms is not None:
        settings["interval_min_ms"] = max(100, min(5000, int(payload.interval_min_ms)))
    if payload.interval_max_ms is not None:
        settings["interval_max_ms"] = max(100, min(8000, int(payload.interval_max_ms)))
    if settings["interval_max_ms"] < settings["interval_min_ms"]:
        settings["interval_max_ms"] = settings["interval_min_ms"]
    store.save_settings(settings)
    return {"ok": True, "settings": settings}
