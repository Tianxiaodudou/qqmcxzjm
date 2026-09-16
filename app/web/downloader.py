"""下载任务管理器：串行执行、双进度（音频 / 元数据）、手动重试。"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import decrypt, env, errors, security, store, tagging

logger = logging.getLogger("qqmusic.download")

PENDING = "pending"
DOWNLOADING = "downloading"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

# 四个阶段：前端按此顺序渲染进度条
STAGES: tuple[tuple[str, str], ...] = (
    ("download", "音频下载"),
    ("meta", "元数据获取"),
    ("decrypt", "解密"),
    ("merge", "元数据合并"),
)

WORK_DIR_NAME = "work"


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
    status: str = DOWNLOADING
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
            self.status = "success"
        else:
            self.status = DOWNLOADING
        self.touch()

    def to_public(self) -> dict[str, Any]:
        """返回给前端的数据（不含任何凭证）。"""
        data = asdict(self)
        data["progress"] = {key: round(float(value), 4) for key, value in self.progress.items()}
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
        # 解密/打标签在子线程执行，_save 需要跨线程安全
        self._save_lock = threading.Lock()

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        self._load()
        if self._worker is None or self._worker.done():
            self._running = True
            self._worker = asyncio.create_task(self._run_worker())
        # 恢复中断的任务：中断的阶段重置为 pending，已完成的阶段保留
        for task in self._ordered():
            for key, _ in STAGES:
                if task.state_of(key) == DOWNLOADING:
                    task.states[key] = PENDING
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
        created: list[DownloadTask] = []
        for song in songs:
            created.append(self.submit(song))
        return created

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.to_public() for t in self._ordered()]

    def get(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def retry(self, task_id: str) -> DownloadTask:
        """手动重试：把失败/中断的阶段重置为 pending，已完成的阶段保留。"""
        task = self._tasks.get(task_id)
        if not task:
            raise errors.BadRequestError("任务不存在")
        for key, _ in STAGES:
            if task.state_of(key) in (FAILED, DOWNLOADING, PENDING):
                task.states[key] = PENDING
                task.progress[key] = 0.0
        if all(task.state_of(key) in (DONE, SKIPPED) for key, _ in STAGES):
            task.fail_reason = None
            task.refresh_status()
            self._save()
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
                self._cleanup_work(tid)
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
            source = str(runtime.get("source") or "").strip()
            return bool(source) and Path(source).is_file()
        if key == "meta":
            return "lyric" in runtime and "cover" in runtime
        if key == "decrypt":
            decrypted = str(runtime.get("decrypted") or "").strip()
            return bool(decrypted) and Path(decrypted).is_file()
        return True

    # ---------------- 阶段一：下载（自动选用账号可用的最高音质） ----------------
    async def _stage_download(
        self,
        task: DownloadTask,
        work_dir: Path,
        target_dir: Path,
        runtime: dict[str, Any],
    ) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        task.set_stage("download", DOWNLOADING, 0.0, "获取歌曲信息")
        self._save()

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

        source = work_dir / "source.bin"
        chosen: dict[str, Any] | None = None
        last_reason = ""
        for quality in env.QUALITY_ORDER:
            try:
                resolved = await self._service.song_url(task.songmid, quality)
            except Exception as exc:  # noqa: BLE001
                last_reason = str(exc)
                logger.info("音质 %s 不可用：%s", quality, security.sanitize_log(last_reason))
                continue
            url = str(resolved.get("url") or "")
            if not url:
                last_reason = "接口未返回播放地址"
                continue
            if resolved.get("encrypted") and not resolved.get("ekey"):
                # 没有 ekey 说明账号拿不到该音质的完整文件（QQ 只会退回试听片段）
                last_reason = "该音质无权限"
                logger.info("音质 %s 无解密密钥，跳过", quality)
                continue

            expected = int(sizes.get(env.QUALITY_SIZE_KEYS.get(quality, ""), 0) or 0)
            task.set_stage("download", None, 0.02, f"下载 {env.quality_label(quality)}")
            self._save()
            size = await self._fetch(task, url, source, expected)
            if expected and resolved.get("encrypted") and size < int(expected * 0.9):
                # 文件明显偏小：账号只能拿到试听片段，降级重试
                last_reason = "该音质仅返回试听片段"
                logger.info("音质 %s 为试听片段（%s < %s），降级", quality, size, expected)
                continue
            chosen = resolved
            task.quality = quality
            task.quality_label = env.quality_label(quality)
            task.encrypted = bool(resolved.get("encrypted"))
            break

        if chosen is None:
            raise errors.UpstreamError(f"没有可用的音质：{last_reason or '未知原因'}")

        runtime["resolved"] = {
            "ekey": str(chosen.get("ekey") or ""),
            "ext": str(chosen.get("ext") or ""),
            "encrypted": bool(chosen.get("encrypted")),
        }
        runtime["source"] = str(source)
        task.received = source.stat().st_size
        task.total = task.received
        task.set_stage("download", DONE, 1.0, "")
        task.refresh_status()
        self._save()

    async def _fetch(self, task: DownloadTask, url: str, dest: Path, expected: int = 0) -> int:
        """流式下载，边下边汇报进度；返回实际字节数。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        received = 0
        reported = 0.0
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client:
            async with client.stream("GET", url) as response_stream:
                response_stream.raise_for_status()
                content_length = int(response_stream.headers.get("Content-Length") or 0)
                total = content_length or expected or 0
                task.total = total
                with open(dest, "wb") as handle:
                    async for chunk in response_stream.aiter_bytes(256 * 1024):
                        handle.write(chunk)
                        received += len(chunk)
                        task.received = received
                        if total:
                            ratio = min(0.99, received / total)
                            if ratio - reported >= 0.05:
                                reported = ratio
                                task.set_stage("download", None, ratio)
                                self._save()
                        else:
                            task.touch()
        if not received:
            raise errors.UpstreamError("下载内容为空")
        return received

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
        task.set_stage("meta", None, 0.92, "整理元数据")
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

        source = Path(str(runtime.get("source") or ""))
        if not source.exists():
            raise errors.UpstreamError("原始音频不存在，请重试")
        resolved = runtime.get("resolved") or {}
        ext = str(resolved.get("ext") or source.suffix or ".bin")
        dest = work_dir / f"decrypted{ext}"

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

        # 纯 Python 解密是 CPU 密集操作，放线程池执行，避免阻塞事件循环（否则进度条会长时间不动）
        result = await asyncio.to_thread(
            decrypt.decrypt_file,
            source,
            dest,
            str(resolved.get("ekey") or ""),
            progress=report,
            encrypted_hint=bool(resolved.get("encrypted")),
        )
        output = Path(str(result.get("output") or dest))
        real_ext = str(result.get("ext") or ext)
        if output.suffix.lower() != real_ext.lower():
            renamed = output.with_suffix(real_ext)
            os.replace(output, renamed)
            output = renamed
        runtime["decrypted"] = str(output)
        runtime["ext"] = real_ext
        task.encrypted = bool(result.get("encrypted"))
        task.output_ext = real_ext
        task.output_size = int(result.get("audio_size") or output.stat().st_size)
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
        task.set_stage("merge", DOWNLOADING, 0.05, "写出音频文件")
        self._save()

        stem = security.sanitize_filename(f"{task.name}_{task.singer}_{task.songmid}", fallback=task.songmid)
        source = Path(str(runtime.get("decrypted") or ""))
        if not source.exists() and work_dir.is_dir():
            found = sorted(work_dir.glob("decrypted.*"))
            if found:
                source = found[0]
                runtime.setdefault("decrypted", str(source))
        ext = str(runtime.get("ext") or source.suffix or ".bin")
        if not ext.startswith("."):
            ext = "." + ext
        final = target_dir / f"{stem}{ext}"
        target_dir.mkdir(parents=True, exist_ok=True)
        if source.exists():
            if final.exists() and final.resolve() != source.resolve():
                final.unlink()
            shutil.move(str(source), str(final))
        elif not final.exists():
            raise errors.UpstreamError("解密后的音频不存在，请重试")
        task.set_stage("merge", None, 0.35, "写入元数据")
        self._save()

        detail = runtime.get("detail") or {}
        lyric = runtime.get("lyric") or {}
        meta = {
            "title": task.name,
            "artist": task.singer,
            "album": task.album or str(detail.get("album") or ""),
            "songmid": task.songmid,
            "songid": task.songid,
            "comment": "QQ音乐下载器",
        }

        def report(value: float) -> None:
            # tagging 的 0~1 映射到本阶段的 0.35~1.0，避免进度条回退
            task.set_stage("merge", None, 0.35 + 0.65 * max(0.0, min(1.0, float(value))))

        try:
            await asyncio.to_thread(
                tagging.embed,
                final,
                meta,
                cover=runtime.get("cover") or b"",
                lyric=str(lyric.get("lyric") or ""),
                translation=str(lyric.get("translation") or ""),
                progress=report,
            )
        except Exception as exc:  # noqa: BLE001
            raise errors.UpstreamError(f"元数据写入失败：{exc}") from exc

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

        store.append_history(
            {
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
                "fail_reason": task.fail_reason,
            }
        )

    def _cleanup_work(self, task_id: str) -> None:
        shutil.rmtree(env.DATA_DIR / WORK_DIR_NAME / task_id, ignore_errors=True)
        self._runtime.pop(task_id, None)

    def _fail(self, task: DownloadTask, reason: str, message: str = "") -> None:
        """失败标记：只标记尚未完成的阶段。"""
        for key, _ in STAGES:
            if task.state_of(key) not in (DONE, SKIPPED):
                task.states[key] = FAILED
        task.fail_reason = reason
        task.message = security.sanitize_log(message)[:300]
        task.refresh_status()
        if reason == errors.CREDENTIAL_EXPIRED:
            store.clear_credentials()
        self._save()

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
            self._tasks[task.id] = task
            self._order.append(task.id)
