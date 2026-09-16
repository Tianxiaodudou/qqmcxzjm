"""下载任务管理器：串行执行、双进度（音频 / 元数据）、手动重试。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import env, errors, security, store

logger = logging.getLogger("qqmusic.download")

PENDING = "pending"
DOWNLOADING = "downloading"
DONE = "done"
FAILED = "failed"

META_STEPS = ("detail", "lyric", "cover", "manifest")


@dataclass
class DownloadTask:
    """一首歌 = 一个任务 = 加密音频文件 + 元数据 JSON 文件。"""

    id: str
    songmid: str
    songid: int
    name: str
    singer: str
    album_pmid: str = ""
    quality: str = ""
    audio_state: str = PENDING
    meta_state: str = PENDING
    status: str = DOWNLOADING
    fail_reason: str | None = None
    audio_progress: float = 0.0
    audio_received: int = 0
    audio_total: int = 0
    meta_progress: float = 0.0
    meta_step: str = ""
    audio_path: str = ""
    meta_path: str = ""
    message: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def touch(self) -> None:
        self.updated_at = int(time.time())

    def refresh_status(self) -> None:
        """任务整体状态由音频与元数据两项共同决定。"""
        states = (self.audio_state, self.meta_state)
        if FAILED in states:
            self.status = FAILED
        elif all(s == DONE for s in states):
            self.status = "success"
        else:
            self.status = DOWNLOADING
        self.touch()

    def to_public(self) -> dict[str, Any]:
        """返回给前端的数据（不含任何凭证）。"""
        data = asdict(self)
        data["audio_progress"] = round(self.audio_progress, 4)
        data["meta_progress"] = round(self.meta_progress, 4)
        return data


class DownloadManager:
    """串行下载队列：音频完成后才开始元数据。"""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._tasks: dict[str, DownloadTask] = {}
        self._order: list[str] = []
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._running = False

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        self._load()
        if self._worker is None or self._worker.done():
            self._running = True
            self._worker = asyncio.create_task(self._run_worker())
        # 恢复中断的任务（音频已完成的跳过）
        for task in self._ordered():
            if task.audio_state == DOWNLOADING:
                task.audio_state = PENDING
            if task.meta_state == DOWNLOADING:
                task.meta_state = PENDING
            if task.status == DOWNLOADING or PENDING in (task.audio_state, task.meta_state):
                self._enqueue(task.id)

    async def stop(self) -> None:
        self._running = False
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._worker = None
        self._save()

    # ---------------- 对外接口 ----------------
    def submit(self, song: dict[str, Any], quality: str = "") -> DownloadTask:
        songmid = str(song.get("songmid") or song.get("mid") or "")
        if not songmid:
            raise errors.BadRequestError("缺少歌曲 mid")
        task_id = f"{songmid}-{int(time.time() * 1000)}"
        task = DownloadTask(
            id=task_id,
            songmid=songmid,
            songid=int(song.get("songid") or song.get("id") or 0),
            name=str(song.get("name") or song.get("title") or songmid),
            singer=str(song.get("singer") or ""),
            album_pmid=str(song.get("album_pmid") or ""),
            quality=quality or store.load_settings().get("quality", env.DEFAULT_QUALITY),
        )
        self._tasks[task_id] = task
        self._order.append(task_id)
        self._enqueue(task_id)
        self._save()
        return task

    def create_batch(self, songs: list[dict[str, Any]], quality: str = "") -> list[DownloadTask]:
        """批量创建：串行入队（风控要求，避免 tight loop）。"""
        created: list[DownloadTask] = []
        for song in songs:
            created.append(self.submit(song, quality))
        return created

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.to_public() for t in self._ordered()]

    def get(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def retry(self, task_id: str) -> DownloadTask:
        """手动重试：只把 failed 的项重置为 pending，done 的保持不动。"""
        task = self._tasks.get(task_id)
        if not task:
            raise errors.BadRequestError("任务不存在")
        if task.audio_state == FAILED:
            task.audio_state = PENDING
            task.audio_received = 0
            task.audio_progress = 0.0
        if task.meta_state == FAILED:
            task.meta_state = PENDING
            task.meta_progress = 0.0
            task.meta_step = ""
        if task.audio_state == DONE and task.meta_state == DONE:
            task.status = "success"
            task.touch()
            return task
        task.fail_reason = None
        task.message = ""
        task.refresh_status()
        self._enqueue(task.id)
        self._save()
        return task

    def clear_finished(self) -> int:
        keep = [tid for tid, t in self._tasks.items() if t.status == DOWNLOADING]
        removed = len(self._tasks) - len(keep)
        for tid in list(self._tasks):
            if tid not in keep:
                del self._tasks[tid]
        self._order = [tid for tid in self._order if tid in self._tasks]
        self._save()
        return removed

    # ---------------- 内部实现 ----------------
    def _ordered(self) -> list[DownloadTask]:
        return [self._tasks[tid] for tid in self._order if tid in self._tasks]

    def _enqueue(self, task_id: str) -> None:
        self._queue.put_nowait(task_id)

    async def _run_worker(self) -> None:
        while self._running:
            try:
                task_id = await self._queue.get()
            except asyncio.CancelledError:
                return
            task = self._tasks.get(task_id)
            if not task:
                continue
            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务异常：%s", security.sanitize_log(str(exc)))
                self._fail(task, errors.classify(exc), str(exc))
            finally:
                self._save()
            # 风控：任务之间加入随机延时，避免请求过于规整
            settings = store.load_settings()
            low = int(settings.get("interval_min_ms", 300))
            high = max(low, int(settings.get("interval_max_ms", 800)))
            await asyncio.sleep(random.uniform(low, high) / 1000.0)

    async def _run_task(self, task: DownloadTask) -> None:
        settings = store.load_settings()
        target_dir = Path(settings.get("download_dir") or env.DATA_DIR / "downloads")

        if task.audio_state != DONE:
            await self._download_audio(task, target_dir)
        if task.audio_state == DONE and task.meta_state != DONE:
            await self._download_metadata(task, target_dir)

    async def _download_audio(self, task: DownloadTask, target_dir: Path) -> None:
        task.audio_state = DOWNLOADING
        task.meta_step = ""
        task.refresh_status()
        self._save()

        quality = task.quality or env.DEFAULT_QUALITY
        # 先取详情：拿到 media_mid、歌手、封面 pmid 与各音质大小
        detail = await self._service.song_detail(task.songmid)
        if detail:
            task.name = detail.get("name") or task.name
            task.singer = detail.get("singer") or task.singer
            task.album_pmid = detail.get("album_pmid") or task.album_pmid
            if detail.get("songid"):
                task.songid = int(detail["songid"])
        resolved = await self._service.song_url(task.songmid, quality)
        url = resolved["url"]
        extension = Path(resolved.get("filename") or "").suffix or ".bin"
        filename = security.sanitize_filename(f"{task.singer} - {task.name}{extension}")

        target_dir.mkdir(parents=True, exist_ok=True)
        audio_path = target_dir / filename
        temp_path = audio_path.with_suffix(audio_path.suffix + ".part")

        sizes = (detail or {}).get("sizes") or {}
        task.audio_total = int(sizes.get(env.QUALITY_SIZE_KEYS.get(quality, ""), 0) or 0)

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client:
            async with client.stream("GET", url) as response_stream:
                response_stream.raise_for_status()
                content_length = int(response_stream.headers.get("Content-Length") or 0)
                if content_length:
                    task.audio_total = content_length
                received = 0
                with open(temp_path, "wb") as handle:
                    async for chunk in response_stream.aiter_bytes(256 * 1024):
                        handle.write(chunk)
                        received += len(chunk)
                        task.audio_received = received
                        task.audio_total = max(task.audio_total, received)
                        task.audio_progress = min(1.0, received / task.audio_total) if task.audio_total else 0.0
                        task.touch()
        os.replace(temp_path, audio_path)

        try:
            os.chmod(audio_path, 0o644)
        except OSError:
            pass

        task.audio_path = str(audio_path)
        task.audio_progress = 1.0
        task.audio_state = DONE
        task.refresh_status()
        self._save()

    async def _download_metadata(self, task: DownloadTask, target_dir: Path) -> None:
        task.meta_state = DOWNLOADING
        task.meta_step = META_STEPS[0]
        task.meta_progress = 0.05
        task.refresh_status()
        self._save()

        settings = store.load_settings()
        want_trans = bool(settings.get("lyric_trans", env.DEFAULT_LYRIC_TRANS))
        detail = await self._service.song_detail(task.songmid)
        task.meta_step = META_STEPS[1]
        task.meta_progress = 0.3
        task.touch()
        lyric = await self._service.song_lyric(task.songid or task.songmid, trans=want_trans)
        task.meta_step = META_STEPS[2]
        task.meta_progress = 0.6
        task.touch()

        cover_path = ""
        pmid = (detail or {}).get("album_pmid") or task.album_pmid
        if pmid:
            cover_path = await self._download_cover(task, target_dir, pmid)
        task.meta_step = META_STEPS[3]
        task.meta_progress = 0.85
        task.touch()

        payload = {
            "songmid": task.songmid,
            "songid": task.songid,
            "name": task.name,
            "singer": task.singer,
            "album": (detail or {}).get("album") or "",
            "album_pmid": pmid,
            "quality": task.quality,
            "lyric_trans": want_trans,
            "lyric": lyric.get("lyric", ""),
            "lyric_translation": lyric.get("translation", ""),
            "cover": cover_path,
            "audio": task.audio_path,
            "generated_at": int(time.time()),
        }
        meta_path = target_dir / security.sanitize_filename(f"{task.singer} - {task.name}.json")
        temp_path = meta_path.with_suffix(".json.part")
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, meta_path)

        task.meta_path = str(meta_path)
        task.meta_progress = 1.0
        task.meta_step = "done"
        task.meta_state = DONE
        task.refresh_status()
        self._save()

        store.append_history(
            {
                "songmid": task.songmid,
                "songid": task.songid,
                "name": task.name,
                "singer": task.singer,
                "status": task.status,
                "audio_state": task.audio_state,
                "meta_state": task.meta_state,
                "audio_path": task.audio_path,
                "meta_path": task.meta_path,
                "quality": task.quality,
                "fail_reason": task.fail_reason,
            }
        )

    async def _download_cover(self, task: DownloadTask, target_dir: Path, pmid: str) -> str:
        url = f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{pmid}.jpg"
        cover_path = target_dir / security.sanitize_filename(f"{task.singer} - {task.name}.jpg")
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                cover_path.write_bytes(response.content)
            return str(cover_path)
        except Exception as exc:  # noqa: BLE001
            logger.info("封面下载失败：%s", security.sanitize_log(str(exc)))
            return ""

    def _fail(self, task: DownloadTask, reason: str, message: str = "") -> None:
        """失败标记：只标记尚未完成的部分。"""
        if task.audio_state != DONE:
            task.audio_state = FAILED
        if task.meta_state != DONE:
            task.meta_state = FAILED
        task.fail_reason = reason
        task.message = security.sanitize_log(message)[:300]
        task.refresh_status()
        if reason == errors.CREDENTIAL_EXPIRED:
            store.clear_credentials()
        self._save()

    # ---------------- 持久化 ----------------
    def _save(self) -> None:
        try:
            data = [t.to_public() for t in self._ordered()]
            store._write_json(env.TASKS_FILE, data, 0o600)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            logger.info("任务持久化失败：%s", security.sanitize_log(str(exc)))

    def _load(self) -> None:
        try:
            raw = store._read_json(env.TASKS_FILE, [])  # noqa: SLF001
        except Exception:  # noqa: BLE001
            raw = []
        if not isinstance(raw, list):
            return
        for item in raw:
            try:
                task = DownloadTask(**{k: v for k, v in item.items() if k in DownloadTask.__dataclass_fields__})
            except TypeError:
                continue
            self._tasks[task.id] = task
            self._order.append(task.id)
