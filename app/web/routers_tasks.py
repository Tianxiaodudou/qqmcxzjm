"""任务队列 / 历史记录 / 设置 相关 API。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from . import env, errors, security, store
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
    quality: str = ""


class SettingsRequest(BaseModel):
    download_dir: str | None = None
    quality: str | None = None
    lyric_trans: bool | None = None
    interval_min_ms: int | None = None
    interval_max_ms: int | None = None


def _validate_dir(raw: str) -> Path:
    """自定义下载目录必须为绝对路径且不能落在系统目录。"""
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
    if env.AUTHORIZED_PATHS and not security.is_authorized_dir(path):
        raise errors.BadRequestError("该目录未获得授权，请点击「选择目录」重新授权")
    return path


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
    quality = payload.quality or store.load_settings().get("quality", env.DEFAULT_QUALITY)
    if quality not in env.QUALITY_MAP:
        raise errors.BadRequestError("不支持该音质")
    created = manager.create_batch(songs, quality)
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
            result[songmid] = {"audio": "unknown", "meta": "unknown", "fingerprint": "none"}
            continue
        fingerprint = "|".join(
            (
                security.file_fingerprint(Path(item.get("audio_path") or "")),
                security.file_fingerprint(Path(item.get("meta_path") or "")),
            )
        )
        with _check_lock:
            cached = _check_cache.get(songmid)
        if cached and cached[0] == fingerprint:
            result[songmid] = cached[1]
            continue
        state = {
            "audio": _file_state(item.get("audio_path") or ""),
            "meta": _file_state(item.get("meta_path") or ""),
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
async def get_settings() -> dict[str, Any]:
    settings = store.load_settings()
    return {
        "ok": True,
        "settings": settings,
        "qualities": [{"value": k, "label": env.quality_label(k)} for k in env.QUALITY_MAP],
    }


@router.post("/settings")
async def update_settings(payload: SettingsRequest) -> dict[str, Any]:
    settings = store.load_settings()
    if payload.download_dir is not None:
        settings["download_dir"] = str(_validate_dir(payload.download_dir))
    if payload.quality is not None:
        if payload.quality not in env.QUALITY_MAP:
            raise errors.BadRequestError("不支持该音质")
        settings["quality"] = payload.quality
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
