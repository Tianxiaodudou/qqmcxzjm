"""已下载文件的本地播放支持：媒体流（Range）、元数据/歌词/内嵌封面。

前端「播放」入口只传 songmid，后端从下载历史里解析出文件路径；
只有当文件位于「已授权目录」或「下载历史记录过的路径」时才允许读取，
避免任意路径读取。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from . import env, fnos_api, security, store
from .context import service

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

logger = logging.getLogger("qqmusic.api.local")
router = APIRouter(tags=["local"])

AUDIO_MIME: dict[str, str] = {
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".ape": "audio/x-ape",
    ".wv": "audio/x-wavpack",
}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 512 * 1024


# --------------------------------------------------------------------------
# 路径解析与访问控制
# --------------------------------------------------------------------------
async def _authorized_dirs(request: Request) -> list[str]:
    """已授权目录（个人授权 + 共享授权 + 启动注入）——复用任务模块的同一套查询。"""
    dirs: list[str] = []
    try:
        from .routers_tasks import _authorized_dirs as _task_authorized_dirs

        data = await _task_authorized_dirs(request)
        dirs = [str(item) for item in (data.get("dirs") or []) if item]
    except Exception as exc:  # noqa: BLE001
        logger.warning("授权目录查询失败：%s", security.sanitize_log(str(exc)))
    for item in env.AUTHORIZED_PATHS:
        if item and item not in dirs:
            dirs.append(item)
    return dirs


def _history_outputs() -> set[str]:
    paths: set[str] = set()
    try:
        for item in store.load_history():
            raw = str(item.get("output") or "")
            if not raw:
                continue
            try:
                paths.add(str(Path(raw).resolve()))
            except OSError:
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("下载历史读取失败：%s", security.sanitize_log(str(exc)))
    return paths


async def _resolve_media(request: Request, songmid: str, raw_path: str) -> Path:
    raw = (raw_path or "").strip()
    if not raw and songmid:
        item = store.find_history(str(songmid))
        raw = str((item or {}).get("output") or "")
    if not raw:
        raise HTTPException(status_code=404, detail="没有找到该歌曲的已下载文件")
    try:
        path = Path(raw).resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="文件路径无效") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在（可能已被移动或删除）")
    allowed_by_history = str(path) in _history_outputs()
    if not allowed_by_history:
        dirs = await _authorized_dirs(request)
        if not security.is_authorized_dir(path, extra=dirs):
            logger.warning("拒绝访问未授权路径：%s", security.sanitize_log(str(path)))
            raise HTTPException(status_code=403, detail="该文件不在已授权文件夹内")
    return path


# --------------------------------------------------------------------------
# 媒体流（支持 Range，浏览器可拖动进度）
# --------------------------------------------------------------------------
@router.get("/local/stream")
async def local_stream(
    request: Request,
    songmid: str = Query(""),
    path: str = Query(""),
) -> StreamingResponse:
    file = await _resolve_media(request, songmid, path)
    size = file.stat().st_size
    media_type = AUDIO_MIME.get(file.suffix.lower(), "application/octet-stream")
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Content-Disposition": "inline",
    }
    start, end = 0, max(0, size - 1)
    status = 200
    range_header = (request.headers.get("range") or "").strip()
    if range_header:
        matched = _RANGE_RE.fullmatch(range_header)
        if matched:
            first, last = matched.group(1), matched.group(2)
            if first:
                start = int(first)
                if last:
                    end = min(int(last), size - 1)
            elif last:  # bytes=-N 取最后 N 字节
                start = max(0, size - int(last))
            if start > end or start >= size:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
                )
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1
    headers["Content-Length"] = str(length)

    def iter_file():
        with file.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    logger.info("本地播放：%s（%s 字节，range=%s）", file.name, size, range_header or "-")
    return StreamingResponse(iter_file(), status_code=status, media_type=media_type, headers=headers)


# --------------------------------------------------------------------------
# 元数据 / 歌词 / 封面读取
# --------------------------------------------------------------------------
def _tag_value(tags: Any, *names: str) -> str:
    for name in names:
        try:
            value = tags.get(name)
        except Exception:  # noqa: BLE001
            value = None
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            first = value[0]
        else:
            first = value
        text = getattr(first, "text", first)
        if isinstance(text, (list, tuple)):
            text = " / ".join(str(t) for t in text)
        text = str(text).strip()
        if text:
            return text
    return ""


def _read_tags(file: Path) -> dict[str, Any]:
    """用 mutagen 读取标签；读取失败时返回空结果，不影响播放。"""
    result: dict[str, Any] = {
        "title": "",
        "artist": "",
        "album": "",
        "lyric": "",
        "translation": "",
        "cover": None,
        "cover_mime": "image/jpeg",
        "duration": 0.0,
        "sample_rate": 0,
        "bits": 0,
        "channels": 0,
        "bitrate": 0,
    }
    try:
        from mutagen import File as MutagenFile
    except Exception:  # noqa: BLE001
        logger.warning("mutagen 不可用，无法读取本地文件标签")
        return result
    try:
        audio = MutagenFile(str(file))
    except Exception as exc:  # noqa: BLE001
        logger.warning("标签读取失败 %s：%s", file.name, security.sanitize_log(str(exc)))
        return result
    if audio is None:
        return result

    info = getattr(audio, "info", None)
    if info is not None:
        result["duration"] = float(getattr(info, "length", 0) or 0)
        result["sample_rate"] = int(getattr(info, "sample_rate", 0) or 0)
        result["bits"] = int(
            getattr(info, "bits_per_sample", 0) or getattr(info, "bit_depth", 0) or 0
        )
        result["channels"] = int(getattr(info, "channels", 0) or 0)
        result["bitrate"] = int(getattr(info, "bitrate", 0) or 0)

    tags = getattr(audio, "tags", None)
    if tags is not None:
        result["title"] = _tag_value(tags, "title", "TIT2", "\xa9nam")
        result["artist"] = _tag_value(tags, "artist", "TPE1", "\xa9ART", "albumartist")
        result["album"] = _tag_value(tags, "album", "TALB", "\xa9alb")
        result["lyric"] = _tag_value(tags, "lyrics", "LYRICS", "\xa9lyr")
        result["translation"] = _tag_value(
            tags,
            "lyric_translation",
            "LYRIC_TRANSLATION",
            "----:com.apple.iTunes:LYRIC_TRANSLATION",
        )
        if not result["lyric"]:
            for key in list(tags.keys()):
                if str(key).upper().startswith("USLT"):
                    value = tags.get(key)
                    value = value[0] if isinstance(value, (list, tuple)) and value else value
                    text = getattr(value, "text", value)
                    if isinstance(text, (list, tuple)):
                        text = " / ".join(str(t) for t in text)
                    result["lyric"] = str(text or "").strip()
                    if result["lyric"]:
                        break
        if not result["translation"]:
            for key in list(tags.keys()):
                if str(key).upper().startswith("TXXX") and "TRANSLATION" in str(key).upper():
                    value = tags.get(key)
                    value = value[0] if isinstance(value, (list, tuple)) and value else value
                    text = getattr(value, "text", value)
                    if isinstance(text, (list, tuple)):
                        text = " / ".join(str(t) for t in text)
                    result["translation"] = str(text or "").strip()
                    if result["translation"]:
                        break
        # 内嵌封面
        pictures = getattr(audio, "pictures", None)
        if pictures:
            picture = pictures[0]
            result["cover"] = bytes(picture.data)
            result["cover_mime"] = str(getattr(picture, "mime", "") or "image/jpeg")
        else:
            for key in list(tags.keys()):
                if str(key).upper().startswith("APIC"):
                    value = tags.get(key)
                    value = value[0] if isinstance(value, (list, tuple)) and value else value
                    data = getattr(value, "data", None)
                    if data:
                        result["cover"] = bytes(data)
                        result["cover_mime"] = str(getattr(value, "mime", "") or "image/jpeg")
                        break
            if result["cover"] is None and "covr" in tags:
                value = tags.get("covr")
                value = value[0] if isinstance(value, (list, tuple)) and value else value
                with_bytes = getattr(value, "__bytes__", None)
                data = bytes(value) if with_bytes else None
                if data:
                    result["cover"] = data
                    result["cover_mime"] = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return result


def _fmt_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


@router.get("/local/meta")
async def local_meta(
    request: Request,
    songmid: str = Query(""),
    path: str = Query(""),
) -> dict[str, Any]:
    file = await _resolve_media(request, songmid, path)
    tags = _read_tags(file)
    stat = file.stat()
    quality = ""
    try:
        item = store.find_history(songmid) if songmid else None
        quality = str((item or {}).get("quality_label") or (item or {}).get("quality") or "")
    except Exception:  # noqa: BLE001
        quality = ""
    bitrate = tags["bitrate"]
    fields: list[dict[str, str]] = [
        {"label": "文件名", "value": file.name},
        {"label": "标题", "value": tags["title"] or file.stem},
        {"label": "歌手", "value": tags["artist"]},
        {"label": "专辑", "value": tags["album"]},
        {"label": "时长", "value": f"{int(tags['duration']) // 60}:{int(tags['duration']) % 60:02d}" if tags["duration"] else ""},
        {"label": "音质", "value": quality or file.suffix.lstrip(".").upper()},
        {
            "label": "规格",
            "value": " ".join(
                part
                for part in (
                    f"{tags['sample_rate'] / 1000:.1f} kHz" if tags["sample_rate"] else "",
                    f"{tags['bits']} bit" if tags["bits"] else "",
                    f"{tags['channels']} ch" if tags["channels"] else "",
                    f"{int(bitrate / 1000)} kbps" if bitrate else "",
                )
                if part
            ),
        },
        {"label": "文件大小", "value": _fmt_size(stat.st_size)},
        {"label": "内嵌歌词", "value": "有（%d 行）" % len([l for l in (tags["lyric"] or "").splitlines() if l.strip()]) if tags["lyric"] else "无"},
        {"label": "内嵌封面", "value": "有" if tags["cover"] else "无"},
    ]
    cover = "cover" in str(tags["cover_mime"]).lower()
    return {
        "ok": True,
        "songmid": songmid,
        "path": str(file),
        "name": file.name,
        "title": tags["title"] or file.stem,
        "artist": tags["artist"],
        "album": tags["album"],
        "duration": tags["duration"],
        "sample_rate": tags["sample_rate"],
        "bits": tags["bits"],
        "channels": tags["channels"],
        "bitrate": bitrate,
        "size": stat.st_size,
        "quality_label": quality,
        "lyric": tags["lyric"],
        "translation": tags["translation"],
        "has_cover": bool(tags["cover"]),
        "cover_url": f"/local/cover?songmid={songmid}" if (songmid and cover) else "",
        "fields": fields,
    }


@router.get("/local/cover")
async def local_cover(
    request: Request,
    songmid: str = Query(""),
    path: str = Query(""),
) -> Response:
    file = await _resolve_media(request, songmid, path)
    tags = _read_tags(file)
    data = tags["cover"]
    if not data:
        raise HTTPException(status_code=404, detail="该文件没有内嵌封面")
    return Response(
        content=data,
        media_type=str(tags["cover_mime"] or "image/jpeg"),
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --------------------------------------------------------------------------
# 在线试听：同源代理
# QQ 音乐直链是 `http://<cdn-ip>/...`（且带 vkey 防盗链），在 https 页面里会被
# 浏览器按「混合内容」直接拦掉——现象就是能显示时长却不出声、进度条不动。
# 这里由后端取直链并代理成同源地址，顺便补上 Referer/UA，兼容 Range。
# --------------------------------------------------------------------------
@router.get("/preview/stream")
async def preview_stream(
    request: Request,
    songmid: str = Query(..., min_length=1),
    quality: str = Query(""),
) -> StreamingResponse:
    data = await service.preview_url(songmid, quality)
    url = str(data.get("url") or "")
    if not url:
        raise HTTPException(status_code=404, detail="未获取到试听地址")
    headers = {
        "User-Agent": _UA,
        "Referer": "https://y.qq.com/",
        "Accept": "*/*",
    }
    range_header = (request.headers.get("range") or "").strip()
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=60.0), follow_redirects=True)
    try:
        upstream = await client.send(client.build_request("GET", url, headers=headers), stream=True)
    except httpx.HTTPError as exc:  # noqa: BLE001
        await client.aclose()
        logger.warning("试听代理失败：%s", security.sanitize_log(str(exc)))
        raise HTTPException(status_code=502, detail="试听直链请求失败（网络或版权限制）") from exc
    if upstream.status_code >= 400:
        code = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        logger.warning("试听源返回 %s：%s", code, security.sanitize_log(url.split("?", 1)[0]))
        raise HTTPException(status_code=502, detail=f"试听源返回 {code}")

    out_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Content-Disposition": "inline",
    }
    for key in ("content-type", "content-length", "content-range"):
        value = upstream.headers.get(key)
        if value:
            out_headers[key] = value
    out_headers.setdefault("content-type", "audio/mpeg")

    async def iter_upstream():
        try:
            async for chunk in upstream.aiter_bytes(_CHUNK):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    logger.info(
        "在线试听代理：%s（%s，range=%s）",
        security.sanitize_log(songmid),
        data.get("quality_label") or data.get("quality") or "-",
        range_header or "-",
    )
    return StreamingResponse(iter_upstream(), status_code=upstream.status_code, headers=out_headers)
