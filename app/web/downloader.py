"""下载任务管理器：串行执行、双进度（音频 / 元数据）、手动重试。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import decrypt, env, errors, notify, security, store, tagging

logger = logging.getLogger("qqmusic.download")

PENDING = "pending"
DOWNLOADING = "downloading"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
TASK_SUCCESS = "success"
PAUSED = "paused"


class TaskPaused(Exception):
    """用户点了「全部暂停」：保留中间文件（.part），稍后可继续下载。"""

# 换音质也救不回来的失败原因：命中即停止逐个音质重试，直接向用户报出原因
FATAL_REASONS = (errors.CREDENTIAL_EXPIRED, errors.NOT_LOGGED_IN, errors.RATELIMITED)

# 四个阶段：前端按此顺序渲染进度条
STAGES: tuple[tuple[str, str], ...] = (
    ("download", "音频下载"),
    ("meta", "元数据获取"),
    ("decrypt", "解密"),
    ("merge", "元数据合并"),
)

WORK_DIR_NAME = "work"

# 未写完的中间文件后缀：查重时忽略（它们不是可播放成品）
PART_SUFFIXES = (".part", ".tmp", ".download", ".crdownload")
# 断点续传：work/<任务ID>/audio.part 存已下到的字节，download.json 存该次下载用的
# 音质 / 直链 / ekey，暂停或进程中断后据此接着下
PART_FILE = "audio.part"
PROGRESS_FILE = "download.json"


def output_stem(name: str, singer: str, songmid: str) -> str:
    """成品文件名（不含扩展名）：歌名_歌手_歌曲ID。

    下载去重与实际写文件都走这里，保证两边算法一致。
    """
    return security.sanitize_filename(f"{name}_{singer}_{songmid}", fallback=songmid or "unnamed")


def default_target_dir() -> Path:
    """下载目录：设置里的自定义目录优先，否则数据目录下的 downloads。"""
    settings = store.load_settings()
    return Path(str(settings.get("download_dir") or "") or (env.DATA_DIR / "downloads"))


@dataclass
class DownloadTask:
    """一首歌 = 一个任务：下载 → 取元数据 → 解密 → 合并，最终只留一个可播放成品。"""

    id: str
    songmid: str
    songid: int
    name: str
    singer: str
    album_pmid: str = ""
    album: str = ""
    quality: str = ""
    quality_label: str = ""
    encrypted: bool = False
    states: dict[str, str] = field(default_factory=lambda: {key: PENDING for key, _ in STAGES})
    progress: dict[str, float] = field(default_factory=lambda: {key: 0.0 for key, _ in STAGES})
    step: str = ""
    received: int = 0
    total: int = 0
    output_path: str = ""
    output_name: str = ""
    output_size: int = 0
    output_ext: str = ""
    cover_embedded: bool = False
    lyric_embedded: bool = False
    meta_field_count: int = 0
    status: str = DOWNLOADING
    paused: bool = False
    fail_reason: str | None = None
    message: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def touch(self) -> None:
        self.updated_at = int(time.time())

    def state_of(self, stage: str) -> str:
        return self.states.get(stage, PENDING)

    def set_stage(
        self,
        stage: str,
        state: str | None = None,
        progress: float | None = None,
        step: str | None = None,
    ) -> None:
        if state is not None:
            self.states[stage] = state
        if progress is not None:
            self.progress[stage] = max(0.0, min(1.0, float(progress)))
        if step is not None:
            self.step = step
        self.touch()

    def refresh_status(self) -> None:
        """任务整体状态：任一阶段失败即失败，全部结束即成功。"""
        values = [self.state_of(key) for key, _ in STAGES]
        if FAILED in values:
            self.status = FAILED
        elif all(s in (DONE, SKIPPED) for s in values):
            self.status = TASK_SUCCESS
        elif self.paused:
            self.status = PAUSED
        else:
            self.status = DOWNLOADING
        self.touch()

    def to_public(self) -> dict[str, Any]:
        """返回给前端的数据（不含任何凭证）。"""
        data = asdict(self)
        data["progress"] = {key: round(float(value), 4) for key, value in self.progress.items()}
        data["fail_reason_text"] = errors.reason_text(self.fail_reason)
        data["stages"] = [
            {
                "key": key,
                "label": label,
                "state": self.state_of(key),
                "progress": data["progress"].get(key, 0.0),
            }
            for key, label in STAGES
        ]
        return data


def build_meta(
    *,
    name: str,
    singer: str,
    album: str,
    songmid: str,
    songid: int,
    detail: dict[str, Any],
    extras: dict[str, Any],
) -> dict[str, Any]:
    """把歌曲详情 + 制作人/榜单等附加信息组装成 tagging 需要的 meta。"""
    singers = [
        str(item.get("name") or "").strip()
        for item in (detail.get("singers") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    tag_items = [str(item).strip() for item in (extras.get("tags") or []) if str(item).strip()]
    return {
        "title": name or str(detail.get("title") or ""),
        "subtitle": detail.get("subtitle"),
        "artist": singer or "、".join(singers),
        "album_artist": str(detail.get("singer") or "") or singer or "、".join(singers),
        "album": album or str(detail.get("album") or ""),
        "album_subtitle": detail.get("album_subtitle"),
        "album_date": detail.get("album_date"),
        "songmid": songmid,
        "songid": songid,
        "trans_name": detail.get("trans_name"),
        "date": detail.get("date"),
        "year": detail.get("year"),
        "genre": detail.get("genre"),
        "language": detail.get("language"),
        "company": detail.get("company"),
        "track_no": detail.get("track_no"),
        "disc_no": detail.get("disc_no"),
        "bpm": detail.get("bpm"),
        "media_mid": detail.get("media_mid"),
        "album_mid": detail.get("album_mid"),
        "mv_id": detail.get("mv_id"),
        "mv_vid": detail.get("mv_vid"),
        "intro": detail.get("intro"),
        "replaygain": detail.get("replaygain"),
        "credits": extras.get("credits") or [],
        "tags": "\n".join(tag_items),
        "fav_show": extras.get("fav_show"),
        "fav_count": extras.get("fav_count"),
        "url": f"https://y.qq.com/n/ryqq/songDetail/{songmid}",
        "tool": "QQ音乐下载器",
        "comment": detail.get("intro") or "QQ音乐下载器",
    }


class DownloadManager:
    """串行下载队列：音频完成后才开始元数据。"""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._tasks: dict[str, DownloadTask] = {}
        self._order: list[str] = []
        # 运行期数据（封面字节/歌词/工作目录等），只在内存中，不落盘、不进下载目录
        self._runtime: dict[str, dict[str, Any]] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._running = False
        # 当前批次统计：一批任务（批量创建 / 重试）全部跑完后推一条「任务完成」通知
        self._batch: dict[str, Any] = {"active": False, "success": 0, "fails": []}
        # 解密/打标签在子线程执行，_save 需要跨线程安全
        self._save_lock = threading.Lock()

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        # 先读回任务列表，再清理中间目录：未完成任务（失败/暂停）的 work 目录要留着续传
        self._load()
        self._prune_workspace()
        if self._worker is None or self._worker.done():
            self._running = True
            self._worker = asyncio.create_task(self._run_worker())
        # 恢复中断的任务：中断的阶段重置为 pending，已完成的阶段保留
        for task in self._ordered():
            for key, _ in STAGES:
                if task.state_of(key) == DOWNLOADING:
                    task.states[key] = PENDING
            if task.paused:
                # 上次是用户主动暂停的：保持暂停，等用户点「全部继续」
                task.refresh_status()
                continue
            if task.status == DOWNLOADING or any(task.state_of(k) == PENDING for k, _ in STAGES):
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
        """创建任务。音质无需指定：会自动选用登录账号可用的最高音质。"""
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
            quality=str(quality or ""),
        )
        self._tasks[task_id] = task
        self._order.append(task_id)
        self._enqueue(task_id)
        self._save()
        return task

    def create_batch(self, songs: list[dict[str, Any]]) -> list[DownloadTask]:
        """批量创建：串行入队（风控要求，避免 tight loop）。"""
        self._batch_open()
        created: list[DownloadTask] = []
        for song in songs:
            created.append(self.submit(song))
        return created

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.to_public() for t in self._ordered()]

    def find_existing_outputs(
        self,
        songs: list[dict[str, Any]],
        target_dir: Path | None = None,
    ) -> dict[str, str]:
        """去重：先算成品文件名，再看下载目录里有没有同名文件。

        只比对文件名（不读内容、不改动磁盘），返回 ``{songmid: 已存在的成品路径}``。
        """
        result: dict[str, str] = {}
        directory = target_dir or default_target_dir()
        try:
            if not directory.is_dir():
                return result
            files = [
                item
                for item in directory.iterdir()
                if item.is_file() and item.suffix.lower() not in PART_SUFFIXES
            ]
        except OSError as exc:  # 目录不可读时不做去重，交给正常下载流程
            logger.info("下载目录不可读，跳过去重：%s", security.sanitize_log(str(exc)))
            return result
        if not files:
            return result
        stems = {item.stem for item in files}
        # 兜底：文件名以「_<歌曲ID>」结尾（例如改名后只剩 ID 部分）也算已下载
        tail_ids = {item.stem.rsplit("_", 1)[-1] for item in files}

        for song in songs:
            songmid = str(song.get("songmid") or song.get("songid") or "")
            if not songmid:
                continue
            stem = output_stem(
                str(song.get("name") or ""), str(song.get("singer") or ""), songmid
            )
            if stem in stems or songmid in tail_ids:
                result[songmid] = str(directory / stem)
        return result

    def get(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def retry(self, task_id: str) -> DownloadTask:
        """手动重试：把失败/中断的阶段重置为 pending，已完成的阶段保留。"""
        task = self._tasks.get(task_id)
        if not task:
            raise errors.BadRequestError("任务不存在")
        self._batch_open()
        for key, _ in STAGES:
            if task.state_of(key) in (FAILED, DOWNLOADING, PENDING):
                task.states[key] = PENDING
                task.progress[key] = 0.0
        if all(task.state_of(key) in (DONE, SKIPPED) for key, _ in STAGES):
            task.paused = False
            task.fail_reason = None
            task.refresh_status()
            self._save()
            return task
        task.paused = False
        task.fail_reason = None
        task.message = ""
        task.refresh_status()
        self._enqueue(task.id)
        self._save()
        return task

    # ---------------- 批量操作（全部重试 / 全部暂停 / 全部继续） ----------------
    def active_count(self) -> int:
        """进行中的任务数（等待中 / 下载中；已暂停与失败不算）。"""
        return sum(1 for task in self._tasks.values() if task.status == DOWNLOADING)

    def pause_all(self) -> int:
        """全部暂停：正在下载的任务在下一个数据块处停下，中间文件保留。"""
        changed = 0
        for task in self._ordered():
            if task.status == DOWNLOADING and not task.paused:
                task.paused = True
                task.message = "已暂停"
                task.touch()
                changed += 1
        if changed:
            self._save()
        return changed

    def resume_all(self) -> int:
        """全部继续：把暂停的任务重新排队（有 .part 的自动续传）。"""
        changed = 0
        for task in self._ordered():
            if not task.paused:
                continue
            task.paused = False
            task.message = ""
            if task.status == PAUSED:
                task.status = DOWNLOADING
            task.touch()
            self._enqueue(task.id)
            changed += 1
        if changed:
            self._save()
        return changed

    def retry_all(self) -> int:
        """全部重试：失败与暂停的任务一起重来（已完成的阶段保留）。"""
        pending = [t.id for t in self._ordered() if t.status in (FAILED, PAUSED)]
        for task_id in pending:
            self.retry(task_id)
        return len(pending)

    def clear_finished(self, include_failed: bool = True) -> int:
        """清理任务记录。

        include_failed=True：保留「运行中」的任务，其余（成功/失败/中断）全部清除 → 「清空全部」
        include_failed=False：只清除已完成（success）的记录，失败/中断的保留下来便于重试 → 「清除已完成」
        """
        def keep(t: DownloadTask) -> bool:
            if t.status in (DOWNLOADING, PAUSED):
                return True
            return (not include_failed) and t.status != TASK_SUCCESS

        keep_ids = [tid for tid, t in self._tasks.items() if keep(t)]
        removed = len(self._tasks) - len(keep_ids)
        for tid in list(self._tasks):
            if tid not in keep_ids:
                del self._tasks[tid]
                self._cleanup_work(tid)
        self._order = [tid for tid in self._order if tid in self._tasks]
        self._save()
        return removed

    def _retire(self, task_id: str) -> None:
        """任务成功后退出任务列表：记录已在「下载历史」里，列表不再保留卡片。"""
        task = self._tasks.get(task_id)
        if not task or task.status != TASK_SUCCESS:
            return
        self._tasks.pop(task_id, None)
        self._order = [tid for tid in self._order if tid != task_id]
        self._cleanup_work(task_id)
        self._save()

    @staticmethod
    def _history_record(task: DownloadTask) -> dict[str, Any]:
        """任务 → 下载历史记录（成功与旧数据迁移共用同一份字段）。"""
        return {
            "songmid": task.songmid,
            "songid": task.songid,
            "name": task.name,
            "singer": task.singer,
            "status": task.status,
            "quality": task.quality,
            "quality_label": task.quality_label,
            "output": task.output_path,
            "encrypted": task.encrypted,
            "cover": task.cover_embedded,
            "lyric": task.lyric_embedded,
            "meta_fields": task.meta_field_count,
            "fail_reason": task.fail_reason,
            "fail_reason_text": errors.reason_text(task.fail_reason),
        }

    @staticmethod
    def _paid_locked(detail: dict[str, Any]) -> bool:
        """详情里的付费标记 + 未支付状态 → 失败原因判定为「需要先购买 / 开通会员」。"""
        pay = (detail or {}).get("pay") or {}
        if not isinstance(pay, dict):
            return False
        try:
            unpaid = int(pay.get("pay_status") or 0) == 0
            needs_pay = int(pay.get("pay_play") or 0) == 1 or int(pay.get("pay_down") or 0) == 1
        except (TypeError, ValueError):
            return False
        return bool(unpaid and needs_pay)

    @staticmethod
    def _resource_missing(detail: dict[str, Any]) -> bool:
        """详情返回「空壳」——无任何音质体积、无 media_mid，连歌名 / songid 都是空的。

        实测：未购买的付费数字专辑（如「黑胶版」）与已下架歌曲都会这样返回，
        此时接口对所有音质一律回 104003（result != 0），失败原因应归到「暂无可用音源」。
        """
        if not detail:
            return False
        if "sizes" not in detail or detail.get("sizes"):
            return False
        if str(detail.get("media_mid") or ""):
            return False
        return not detail.get("songid") or not detail.get("name")

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
            if task.paused:
                # 暂停中的任务不执行（保留 .part，继续时接着下）
                continue
            try:
                await self._run_task(task)
            except TaskPaused:
                self._pause(task)
                continue
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务异常：%s", security.sanitize_log(str(exc)))
                self._fail(task, errors.classify(exc), str(exc))
            finally:
                self._save()
            # 队列排空且没有进行中的任务 → 这一批跑完了，推一条「任务完成」通知
            if self._queue.empty() and not self.active_count():
                self._batch_finish()
            # 风控：任务之间加入随机延时，避免请求过于规整
            settings = store.load_settings()
            low = int(settings.get("interval_min_ms", 300))
            high = max(low, int(settings.get("interval_max_ms", 800)))
            await asyncio.sleep(random.uniform(low, high) / 1000.0)

    async def _run_task(self, task: DownloadTask) -> None:
        settings = store.load_settings()
        target_dir = Path(settings.get("download_dir") or env.DATA_DIR / "downloads")
        work_dir = env.DATA_DIR / WORK_DIR_NAME / task.id
        target_dir.mkdir(parents=True, exist_ok=True)

        runtime = self._runtime.setdefault(task.id, {})
        pipeline = (
            ("download", self._stage_download),
            ("meta", self._stage_metadata),
            ("decrypt", self._stage_decrypt),
            ("merge", self._stage_merge),
        )
        for key, handler in pipeline:
            if task.state_of(key) in (DONE, SKIPPED) and self._stage_ready(key, runtime):
                continue
            task.states[key] = PENDING
            await handler(task, work_dir, target_dir, runtime)

    @staticmethod
    def _stage_ready(key: str, runtime: dict[str, Any]) -> bool:
        """已完成阶段在本次运行中是否仍有可用数据（进程重启后内存数据会丢）。"""
        if key == "download":
            data = runtime.get("audio")
            return isinstance(data, (bytes, bytearray)) and len(data) > 0
        if key == "meta":
            return "lyric" in runtime and "cover" in runtime
        if key == "decrypt":
            data = runtime.get("decrypted")
            return isinstance(data, (bytes, bytearray)) and len(data) > 0
        return True

    # ---------------- 阶段一：下载（自动选用账号可用的最高音质） ----------------
    async def _stage_download(
        self,
        task: DownloadTask,
        work_dir: Path,
        target_dir: Path,
        runtime: dict[str, Any],
    ) -> None:
        task.set_stage("download", DOWNLOADING, 0.0, "获取歌曲信息")
        self._save()

        work_dir.mkdir(parents=True, exist_ok=True)
        part_path = work_dir / PART_FILE
        detail = await self._service.song_detail(task.songmid) or {}
        runtime["detail"] = detail
        if detail:
            task.name = str(detail.get("name") or task.name)
            task.singer = str(detail.get("singer") or task.singer)
            task.album = str(detail.get("album") or task.album)
            task.album_pmid = str(detail.get("album_pmid") or task.album_pmid)
            if detail.get("songid"):
                task.songid = int(detail["songid"])
        sizes = detail.get("sizes") or {}

        data = b""
        chosen: dict[str, Any] | None = None
        last_reason = ""
        last_code = errors.NO_URL
        # 断点续传：上次暂停 / 崩溃留下的 audio.part 直接接着下，不必从头再来
        resumed = self._load_progress(work_dir)
        if resumed and part_path.is_file() and part_path.stat().st_size > 0:
            task.set_stage("download", None, 0.01, "继续上次的下载")
            self._save()
            try:
                data = await self._fetch(
                    task,
                    str(resumed.get("url") or ""),
                    int(resumed.get("expected") or 0),
                    part_path,
                )
                chosen = resumed
                task.quality = str(resumed.get("quality") or "")
                task.quality_label = env.quality_label(task.quality)
                task.encrypted = bool(resumed.get("encrypted"))
            except TaskPaused:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info("续传失败，改为重新下载：%s", security.sanitize_log(str(exc)))
                self._drop_progress(work_dir)
                data, chosen = b"", None
        elif resumed:
            self._drop_progress(work_dir)

        for quality in env.QUALITY_ORDER:
            if chosen is not None:
                break
            try:
                resolved = await self._service.song_url(task.songmid, quality)
            except Exception as exc:  # noqa: BLE001
                last_reason = str(exc)
                last_code = errors.classify(exc)
                logger.info("音质 %s 不可用：%s", quality, security.sanitize_log(last_reason))
                if last_code in FATAL_REASONS:
                    break
                continue
            url = str(resolved.get("url") or "")
            if not url:
                last_reason = "接口未返回播放地址"
                last_code = errors.NO_URL
                continue
            if resolved.get("encrypted") and not resolved.get("ekey"):
                # 没有 ekey 说明账号拿不到该音质的完整文件（QQ 只会退回试听片段）
                last_reason = "该音质无权限"
                last_code = errors.NO_PERMISSION
                logger.info("音质 %s 无解密密钥，跳过", quality)
                continue

            expected = int(sizes.get(env.QUALITY_SIZE_KEYS.get(quality, ""), 0) or 0)
            task.set_stage("download", None, 0.02, f"下载 {env.quality_label(quality)}")
            self._save()
            # 中间产物写进 work/audio.part：暂停或断网后可以从这里接着下；
            # 下完再读回内存用于解密，最终落盘的只有成品文件
            part_path.unlink(missing_ok=True)
            self._write_progress(
                work_dir,
                {
                    "quality": quality,
                    "url": url,
                    "ekey": str(resolved.get("ekey") or ""),
                    "ext": str(resolved.get("ext") or ""),
                    "encrypted": bool(resolved.get("encrypted")),
                    "expected": expected,
                },
            )
            audio = await self._fetch(task, url, expected, part_path)
            size = len(audio)
            if expected and resolved.get("encrypted") and size < int(expected * 0.9):
                # 文件明显偏小：账号只能拿到试听片段，降级重试
                last_reason = "该音质仅返回试听片段"
                last_code = errors.PREVIEW_ONLY
                logger.info("音质 %s 为试听片段（%s < %s），降级", quality, size, expected)
                continue
            data = audio
            chosen = resolved
            task.quality = quality
            task.quality_label = env.quality_label(quality)
            task.encrypted = bool(resolved.get("encrypted"))
            self._drop_progress(work_dir)
            break

        if chosen is None:
            if last_code == errors.NO_PERMISSION and self._paid_locked(detail):
                # 详情里的付费标记说明真正原因是「尚未购买 / 会员等级不够」，
                # 此时接口回的“音质不可用”会误导用户，直接用统一文案
                raise errors.DownloadFailure(errors.PAID_REQUIRED)
            if last_code == errors.NO_PERMISSION and self._resource_missing(detail):
                # 详情是空壳：QQ 对这首歌没有任何可用音源（付费数字专辑未购买 / 已下架）
                raise errors.DownloadFailure(errors.SONG_UNAVAILABLE)
            raise errors.DownloadFailure(
                last_code, last_reason or errors.reason_text(last_code)
            )

        runtime["resolved"] = {
            "ekey": str(chosen.get("ekey") or ""),
            "ext": str(chosen.get("ext") or ""),
            "encrypted": bool(chosen.get("encrypted")),
        }
        runtime["audio"] = data
        runtime["source_size"] = len(data)
        task.received = len(data)
        task.total = len(data)
        task.set_stage("download", DONE, 1.0, "")
        task.refresh_status()
        self._save()

    def _write_progress(self, work_dir: Path, info: dict[str, Any]) -> None:
        """记录本次下载的音质 / 直链 / ekey，供中断后续传使用。"""
        try:
            (work_dir / PROGRESS_FILE).write_text(
                json.dumps(info, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # noqa: BLE001
            logger.info("续传信息写入失败：%s", security.sanitize_log(str(exc)))

    @staticmethod
    def _load_progress(work_dir: Path) -> dict[str, Any]:
        path = work_dir / PROGRESS_FILE
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _drop_progress(work_dir: Path) -> None:
        """丢掉中间产物（下载成功或续传失败时调用）。"""
        (work_dir / PART_FILE).unlink(missing_ok=True)
        (work_dir / PROGRESS_FILE).unlink(missing_ok=True)

    def _report(self, task: DownloadTask, received: int, total: int, reported: float) -> tuple[int, float]:
        """回报下载进度：每涨 5% 落一次盘，避免频繁写任务文件。"""
        task.received = received
        if total:
            ratio = min(0.99, received / total)
            if ratio - reported >= 0.05:
                reported = ratio
                task.set_stage("download", None, ratio)
                self._save()
        else:
            task.touch()
        return received, reported

    async def _fetch(
        self,
        task: DownloadTask,
        url: str,
        expected: int = 0,
        part_path: Path | None = None,
    ) -> bytes:
        """下载音频并汇报进度；支持 Range 断点续传；用户暂停时抛 TaskPaused。

        中间文件写在 `part_path`（work/audio.part），完成后读回内存交给解密阶段，
        这样暂停 / 断网 / 进程重启都不会白下已经拿到的部分。
        """
        if not url:
            raise errors.DownloadFailure(errors.NO_URL)
        existing = 0
        if part_path is not None:
            try:
                existing = part_path.stat().st_size
            except OSError:
                existing = 0
            if expected and existing >= expected:
                # 上次其实已经下完了，只差没走到下一步：直接读回
                return part_path.read_bytes()
        headers = {"Range": f"bytes={existing}-"} if existing else None
        reported = 0.0
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response_stream:
                if response_stream.status_code == 416:
                    # 服务端不接受这个续传起点：丢掉残留，让调用方重新下载
                    if part_path is not None:
                        part_path.unlink(missing_ok=True)
                    raise errors.DownloadFailure(errors.EMPTY_AUDIO)
                response_stream.raise_for_status()
                content_length = int(response_stream.headers.get("Content-Length") or 0)
                if existing and not response_stream.headers.get("Content-Range"):
                    # 服务端忽略了 Range 头，只能整段重下
                    if part_path is not None:
                        part_path.unlink(missing_ok=True)
                    existing = 0
                total = (content_length + existing) if content_length else (expected or 0)
                task.total = total
                buffer = bytearray()
                received = existing
                if part_path is None:
                    async for chunk in response_stream.aiter_bytes(256 * 1024):
                        if task.paused:
                            raise TaskPaused()
                        buffer.extend(chunk)
                        received += len(chunk)
                        received, reported = self._report(task, received, total, reported)
                else:
                    with part_path.open("ab" if existing else "wb") as fp:
                        async for chunk in response_stream.aiter_bytes(256 * 1024):
                            if task.paused:
                                fp.flush()
                                raise TaskPaused()
                            fp.write(chunk)
                            received += len(chunk)
                            received, reported = self._report(task, received, total, reported)
                        fp.flush()
                    buffer = bytearray(part_path.read_bytes())
        if not buffer:
            raise errors.DownloadFailure(errors.EMPTY_AUDIO)
        return bytes(buffer)

    # ---------------- 阶段二：元数据（详情 / 歌词 / 封面） ----------------
    async def _stage_metadata(
        self,
        task: DownloadTask,
        work_dir: Path,
        target_dir: Path,
        runtime: dict[str, Any],
    ) -> None:
        task.set_stage("meta", DOWNLOADING, 0.08, "获取歌曲信息")
        self._save()

        detail = runtime.get("detail") or {}
        task.set_stage("meta", None, 0.3, "获取歌词")
        self._save()

        settings = store.load_settings()
        want_trans = bool(settings.get("lyric_trans", env.DEFAULT_LYRIC_TRANS))
        try:
            lyric = await self._service.song_lyric(task.songid or task.songmid, trans=want_trans)
        except Exception as exc:  # noqa: BLE001
            logger.info("歌词获取失败：%s", security.sanitize_log(str(exc)))
            lyric = {}
        runtime["lyric"] = lyric or {}
        task.set_stage("meta", None, 0.55, "整理歌词")
        self._save()
        task.set_stage("meta", None, 0.7, "获取封面")
        self._save()

        cover = b""
        pmid = str(detail.get("album_pmid") or task.album_pmid or "")
        if pmid:
            cover = await self._fetch_cover(pmid)
        runtime["cover"] = cover
        if bool(settings.get("meta_full", env.DEFAULT_META_FULL)):
            task.set_stage("meta", None, 0.94, "获取制作人/榜单信息")
            self._save()
            try:
                runtime["extras"] = await self._service.song_extras(
                    task.songmid, int(task.songid or 0)
                )
            except Exception as exc:  # noqa: BLE001
                logger.info("附加元数据获取失败：%s", security.sanitize_log(str(exc)))
                runtime["extras"] = {}
        task.set_stage("meta", None, 0.97, "整理元数据")
        task.cover_embedded = bool(cover)
        task.lyric_embedded = bool((lyric or {}).get("lyric"))
        task.set_stage("meta", DONE, 1.0, "")
        task.refresh_status()
        self._save()

    async def _fetch_cover(self, pmid: str) -> bytes:
        url = f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{pmid}.jpg"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
            return response.content
        except Exception as exc:  # noqa: BLE001
            logger.info("封面下载失败：%s", security.sanitize_log(str(exc)))
            return b""

    # ---------------- 阶段三：解密 ----------------
    async def _stage_decrypt(
        self,
        task: DownloadTask,
        work_dir: Path,
        target_dir: Path,
        runtime: dict[str, Any],
    ) -> None:
        task.set_stage("decrypt", DOWNLOADING, 0.0, "解密音频")
        self._save()

        data = runtime.get("audio")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise errors.DownloadFailure(errors.NO_AUDIO)
        resolved = runtime.get("resolved") or {}
        ext = str(resolved.get("ext") or ".bin")
        if not ext.startswith("."):
            ext = "." + ext

        def report(done: int, total: int) -> None:
            task.received = int(done)
            if total:
                task.total = int(total)
            ratio = (float(done) / float(total)) if total else 0.0
            if ratio - task.progress.get("decrypt", 0.0) >= 0.01 or ratio >= 1.0:
                task.set_stage("decrypt", None, ratio)
                self._save()
            else:
                task.touch()

        # 解密是 CPU 密集操作：装了 C 加速库（csrc/qmc2_fast.c）时走 C，否则纯 Python；
        # 都在线程池里跑，避免阻塞事件循环（否则进度条会长时间不动）。全程内存，不写临时文件。
        try:
            result = await asyncio.to_thread(
                decrypt.decrypt_bytes,
                bytes(data),
                str(resolved.get("ekey") or ""),
                progress=report,
                encrypted_hint=bool(resolved.get("encrypted")),
            )
        except Exception as exc:  # noqa: BLE001
            # DecryptError 自带中文说明（密钥失效 / 尾部异常等），直接作为失败提示
            raise errors.DownloadFailure(errors.DECRYPT_FAILED, str(exc)) from exc
        runtime.pop("audio", None)
        runtime["decrypted"] = bytes(result.get("audio") or b"")
        real_ext = str(result.get("ext") or ext)
        if not real_ext.startswith("."):
            real_ext = "." + real_ext
        runtime["ext"] = real_ext
        task.encrypted = bool(result.get("encrypted"))
        task.output_ext = real_ext
        task.output_size = int(result.get("audio_size") or len(runtime["decrypted"]))
        task.set_stage("decrypt", DONE, 1.0, "")
        task.refresh_status()
        self._save()

    # ---------------- 阶段四：元数据合并（写出成品） ----------------
    async def _stage_merge(
        self,
        task: DownloadTask,
        work_dir: Path,
        target_dir: Path,
        runtime: dict[str, Any],
    ) -> None:
        task.set_stage("merge", DOWNLOADING, 0.05, "写入元数据")
        self._save()

        data = runtime.get("decrypted")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise errors.DownloadFailure(errors.NO_AUDIO, "解密后的音频不存在，请重试")
        ext = str(runtime.get("ext") or ".bin")
        if not ext.startswith("."):
            ext = "." + ext
        stem = output_stem(task.name, task.singer, task.songmid)
        final = target_dir / f"{stem}{ext}"
        target_dir.mkdir(parents=True, exist_ok=True)
        task.set_stage("merge", None, 0.35, "写入元数据")
        self._save()

        detail = runtime.get("detail") or {}
        extras = runtime.get("extras") or {}
        lyric = runtime.get("lyric") or {}
        settings = store.load_settings()
        meta = build_meta(
            name=task.name,
            singer=task.singer,
            album=task.album,
            songmid=task.songmid,
            songid=task.songid,
            detail=detail,
            extras=extras,
        )

        def report(value: float) -> None:
            # tagging 的 0~1 映射到本阶段的 0.35~1.0，避免进度条回退
            task.set_stage("merge", None, 0.35 + 0.65 * max(0.0, min(1.0, float(value))))

        try:
            result = await asyncio.to_thread(
                tagging.embed_bytes,
                bytes(data),
                ext,
                meta,
                cover=runtime.get("cover") or b"",
                lyric=str(lyric.get("lyric") or ""),
                translation=str(lyric.get("translation") or ""),
                progress=report,
            )
        except Exception as exc:  # noqa: BLE001
            raise errors.DownloadFailure(errors.TAG_FAILED, f"元数据写入失败：{exc}") from exc
        runtime.pop("decrypted", None)
        # 中间产物不落盘：标签全部写好后，才把成品写入下载目录。
        # 先写同目录临时文件再原子改名，中途失败也不会留下半个文件。
        payload = bytes((result or {}).get("audio") or b"")
        tmp = final.with_name(f".{final.name}.tmp")
        try:
            tmp.write_bytes(payload)
            os.replace(tmp, final)
        except OSError as exc:  # noqa: BLE001
            try:
                tmp.unlink()
            except OSError:
                pass
            raise errors.DownloadFailure(errors.WRITE_FAILED, f"成品写入失败：{exc}") from exc
        written = list((result or {}).get("fields") or [])
        task.meta_field_count = len(written)
        if bool(settings.get("meta_json", env.DEFAULT_META_JSON)):
            self._write_meta_sidecar(final, meta, detail, extras, lyric, written)

        task.output_path = str(final)
        task.output_name = final.name
        task.output_size = final.stat().st_size
        task.received = task.output_size
        task.total = task.output_size
        task.message = f"已输出 {final.name}"
        task.set_stage("merge", DONE, 1.0, "")
        task.refresh_status()
        self._cleanup_work(task.id)
        self._save()

        store.append_history(self._history_record(task))
        notify.notify_download_success(task)
        self._batch_note_success()

        # 完成即「退休」：成功记录只留在「下载历史」里，任务列表不再堆积已完成的卡片。
        # 失败/中断的任务仍留在列表里，方便点「重试」。
        self._retire(task.id)

    def _write_meta_sidecar(
        self,
        audio: Path,
        meta: dict[str, Any],
        detail: dict[str, Any],
        extras: dict[str, Any],
        lyric: dict[str, Any],
        written: list[str],
    ) -> None:
        """按需在音频旁写一份同名 .json，保存该歌曲 id 对应的完整元数据。"""
        path = audio.with_suffix(".json")
        payload = {
            "source": "QQ音乐",
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "songmid": meta.get("songmid"),
            "songid": meta.get("songid"),
            "url": meta.get("url"),
            "fields": {k: v for k, v in meta.items() if v not in (None, "", [], {})},
            "written_tags": written,
            "detail": detail,
            "extras": extras,
            "lyric": {
                "lyric": str(lyric.get("lyric") or ""),
                "translation": str(lyric.get("translation") or ""),
            },
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            logger.info("元数据 json 写入失败：%s", security.sanitize_log(str(exc)))

    def _cleanup_work(self, task_id: str) -> None:
        shutil.rmtree(env.DATA_DIR / WORK_DIR_NAME / task_id, ignore_errors=True)
        self._runtime.pop(task_id, None)

    def _prune_workspace(self) -> None:
        """运行期资源自清理：清掉已结束任务的 work 目录与历史遗留中间文件。

        未完成任务（等待中 / 下载中 / 已暂停 / 失败）的 work 目录要留着 ——
        里面的 audio.part 是断点续传的现场；已完成任务的中间产物一律删除。
        """
        keep = {t.id for t in self._tasks.values() if t.status != TASK_SUCCESS}
        work_root = env.DATA_DIR / WORK_DIR_NAME
        if work_root.is_dir():
            for item in work_root.iterdir():
                if item.name in keep:
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    try:
                        item.unlink()
                    except OSError:
                        continue
        for tmp_dir in (env.DATA_DIR / "tmp", env.DATA_DIR / "temp"):
            if tmp_dir.is_dir():
                shutil.rmtree(tmp_dir, ignore_errors=True)
        try:
            target = default_target_dir()
        except Exception:  # noqa: BLE001
            return
        if not target.is_dir():
            return
        for item in target.iterdir():
            if item.is_file() and item.suffix.lower() in PART_SUFFIXES:
                try:
                    item.unlink()
                except OSError:
                    continue

    def _pause(self, task: DownloadTask) -> None:
        """暂停落账：未完成的阶段回到 pending（保留 .part 中间文件），继续时接着下。"""
        task.paused = True
        for key, _ in STAGES:
            if task.state_of(key) == DOWNLOADING:
                task.states[key] = PENDING
        if task.status != TASK_SUCCESS:
            task.status = PAUSED
        task.fail_reason = None
        task.message = "已暂停"
        task.touch()
        self._save()

    def _fail(self, task: DownloadTask, reason: str, message: str = "") -> None:
        """失败标记：只标记尚未完成的阶段。"""
        for key, _ in STAGES:
            if task.state_of(key) not in (DONE, SKIPPED):
                task.states[key] = FAILED
        task.fail_reason = reason
        # 提示文案统一取自 errors.REASON_TEXT：没有具体信息时也绝不把英文原因码甩给用户
        task.message = security.sanitize_log(message or errors.reason_text(reason))[:300]
        task.refresh_status()
        if reason == errors.CREDENTIAL_EXPIRED:
            store.clear_credentials()
            notify.notify_login_expired(task.message or "请重新登录")
        else:
            notify.notify_task_failure(task, task.message)
        self._batch_note_failure(task, task.message or errors.reason_text(reason))
        self._save()

    # ---------------- 批次统计（一批任务全部结束后推「任务完成」） ----------------
    def _batch_open(self) -> None:
        """确保当前批次处于开启状态（同一批任务只开启一次）。"""
        if not self._batch.get("active"):
            self._batch = {"active": True, "success": 0, "fails": []}

    def _batch_note_success(self) -> None:
        if self._batch.get("active"):
            self._batch["success"] = int(self._batch.get("success") or 0) + 1

    def _batch_note_failure(self, task: DownloadTask, reason: str) -> None:
        if not self._batch.get("active"):
            return
        name = str(getattr(task, "name", "") or getattr(task, "songmid", "") or "未知歌曲")
        singer = str(getattr(task, "singer", "") or "")
        label = f"{name} - {singer}" if singer else name
        self._batch.setdefault("fails", []).append((label, str(reason or "")))

    def _batch_finish(self) -> None:
        """队列排空时收尾：推送「任务完成」（成功/失败数量 + 失败原因）。"""
        batch = self._batch
        if not batch.get("active"):
            return
        success = int(batch.get("success") or 0)
        fails = list(batch.get("fails") or [])
        self._batch = {"active": False, "success": 0, "fails": []}
        if success or fails:
            notify.notify_batch_done(success, fails)
        notify.flush_all()

    # ---------------- 持久化 ----------------
    def _save(self) -> None:
        with self._save_lock:
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
            if task.status == TASK_SUCCESS:
                # 旧版本把「已完成」任务留在任务列表里：升级后不再展示，
                # 历史里还没有这条记录时补一条，避免老记录凭空消失。
                if not store.find_history(task.songmid):
                    store.append_history(self._history_record(task))
                self._cleanup_work(task.id)
                continue
            self._tasks[task.id] = task
            self._order.append(task.id)
