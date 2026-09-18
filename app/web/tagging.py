"""把元数据（标题 / 歌手 / 专辑 / 曲目号 / 制作人 / 封面 / 歌词…）嵌入音频文件。

只写标签，不重新编码音频，音质与原文件完全一致。
支持 FLAC、OGG Vorbis、MP3、M4A 四种容器；标签库使用内置的 mutagen（vendor 目录）。

写法约定：所有容器共用同一份「规范字段」（见 `_canonical`），
再按容器映射到 Vorbis 注释 / ID3 帧 / MP4 atom，保证各格式写入的信息量一致。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[float], None] | None

# 制作人角色 → 专用标签键（其余角色汇总写入 credits）
CREDIT_KEYS = {
    "作词": "lyricist",
    "作曲": "composer",
    "编曲": "arranger",
    "制作人": "producer",
    "演唱": "performer",
    "混音": "mixer",
    "录音": "engineer",
}

# ID3 映射：规范键 → ("text", 帧) / ("txxx", 描述) / ("comm", 描述) / ("url", 描述)
ID3_MAP = {
    "title": ("text", "TIT2"),
    "subtitle": ("text", "TIT3"),
    "artist": ("text", "TPE1"),
    "albumartist": ("text", "TPE2"),
    "album": ("text", "TALB"),
    "album_subtitle": ("txxx", "ALBUM_SUBTITLE"),
    "date": ("text", "TDRC"),
    "originaldate": ("text", "TDOR"),
    "genre": ("text", "TCON"),
    "language": ("text", "TLAN"),
    "company": ("text", "TPUB"),
    "copyright": ("text", "TCOP"),
    "track_no": ("text", "TRCK"),
    "disc_no": ("text", "TPOS"),
    "bpm": ("text", "TBPM"),
    "lyricist": ("text", "TEXT"),
    "composer": ("text", "TCOM"),
    "arranger": ("txxx", "ARRANGER"),
    "producer": ("txxx", "PRODUCER"),
    "performer": ("txxx", "PERFORMER"),
    "engineer": ("txxx", "ENGINEER"),
    "mixer": ("txxx", "MIXER"),
    "credits": ("txxx", "CREDITS"),
    "tags": ("txxx", "QQMUSIC_TAGS"),
    "intro": ("txxx", "INTRO"),
    "songmid": ("txxx", "QQMUSIC_SONGMID"),
    "songid": ("txxx", "QQMUSIC_SONGID"),
    "media_mid": ("txxx", "QQMUSIC_MEDIA_MID"),
    "mv_vid": ("txxx", "QQMUSIC_MV_VID"),
    "url": ("txxx", "QQMUSIC_URL"),
    "replaygain_track_gain": ("txxx", "REPLAYGAIN_TRACK_GAIN"),
    "replaygain_track_peak": ("txxx", "REPLAYGAIN_TRACK_PEAK"),
    "replaygain_track_range": ("txxx", "REPLAYGAIN_TRACK_RANGE"),
    "tool": ("text", "TSSE"),
}

# MP4 映射：规范键 → atom（None 表示走自由格式 ----:com.apple.iTunes:<KEY>）
M4A_MAP = {
    "title": "\xa9nam",
    "subtitle": "\xa9st3",
    "artist": "\xa9ART",
    "albumartist": "aART",
    "album": "\xa9alb",
    "date": "\xa9day",
    "genre": "\xa9gen",
    "company": None,
    "language": None,
    "track_no": "trkn",
    "disc_no": "disk",
    "bpm": "tmpo",
    "copyright": "cprt",
    "composer": "\xa9wrt",
    "tool": "\xa9too",
}

# Vorbis（FLAC / OGG）键名与众数字段
VORBIS_ALIASES = {
    "track_no": "tracknumber",
    "disc_no": "discnumber",
    "company": "organization",
    "tags": "qqmusic_tags",
    "intro": "qqmusic_intro",
}


class TaggingError(Exception):
    """标签写入失败。"""


def _rewind(handle: Any) -> None:
    """mutagen 保存时不会把文件指针回卷，内存文件（BytesIO）需手动 seek(0)。"""
    seek = getattr(handle, "seek", None)
    if callable(seek):
        seek(0)


def _mutagen():
    try:
        import mutagen  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise TaggingError("缺少标签库 mutagen") from exc
    return mutagen


def _image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF":
        return "image/webp"
    return "image/jpeg"


def _text(value) -> str:
    return str(value or "").strip()


def _report(progress: ProgressFn, value: float) -> None:
    if progress:
        progress(max(0.0, min(1.0, value)))


def _credit_map(meta: dict) -> dict[str, str]:
    """制作人名单 → {规范键: 名字串}，如 {"lyricist": "周杰伦"}。"""
    result: dict[str, str] = {}
    for item in meta.get("credits") or []:
        if not isinstance(item, dict):
            continue
        role = _text(item.get("role"))
        names = [str(n).strip() for n in (item.get("names") or []) if str(n).strip()]
        if not role or not names:
            continue
        key = CREDIT_KEYS.get(role)
        if key and key not in result:
            result[key] = "、".join(names)
    return result


def _credits_text(meta: dict) -> str:
    """全部制作角色的文本汇总（含吉他、贝斯等无标准标签的角色）。"""
    lines: list[str] = []
    for item in meta.get("credits") or []:
        if not isinstance(item, dict):
            continue
        role = _text(item.get("role"))
        names = [str(n).strip() for n in (item.get("names") or []) if str(n).strip()]
        if role and names:
            lines.append(f"{role}：{'、'.join(names)}")
    return "\n".join(lines)


def _canonical(meta: dict) -> dict[str, str]:
    """把下载任务里的 meta 归一化成规范字段表（空值不保留）。"""
    rg = meta.get("replaygain") or {}
    if not isinstance(rg, dict):
        rg = {}
    track_no = _text(meta.get("track_no"))
    total = _text(meta.get("total_tracks") or meta.get("track_total"))
    disc_no = _text(meta.get("disc_no"))
    disc_total = _text(meta.get("total_discs") or meta.get("disc_total"))
    data = {
        "title": _text(meta.get("title") or meta.get("name")),
        "subtitle": _text(meta.get("subtitle")),
        "artist": _text(meta.get("artist") or meta.get("singer")),
        "albumartist": _text(meta.get("album_artist") or meta.get("artist") or meta.get("singer")),
        "album": _text(meta.get("album")),
        "album_subtitle": _text(meta.get("album_subtitle")),
        "tool": _text(meta.get("tool")),
        "trans_name": _text(meta.get("trans_name")),
        "album_date": _text(meta.get("album_date")),
        "fav_show": _text(meta.get("fav_show")),
        "fav_count": _text(meta.get("fav_count")),
        "date": _text(meta.get("date") or meta.get("year")),
        "originaldate": _text(meta.get("originaldate") or meta.get("year")),
        "genre": _text(meta.get("genre")),
        "language": _text(meta.get("language")),
        "company": _text(meta.get("company")),
        "copyright": _text(meta.get("copyright") or meta.get("company")),
        "track_no": f"{track_no}/{total}" if (track_no and total) else track_no,
        "disc_no": f"{disc_no}/{disc_total}" if (disc_no and disc_total) else disc_no,
        "bpm": _text(meta.get("bpm")),
        "songmid": _text(meta.get("songmid")),
        "songid": _text(meta.get("songid")),
        "media_mid": _text(meta.get("media_mid")),
        "mv_vid": _text(meta.get("mv_vid")),
        "url": _text(meta.get("url")),
        "intro": _text(meta.get("intro")),
        "tags": _text(meta.get("tags")),
        "credits": _credits_text(meta),
        "replaygain_track_gain": _text(_rg_text(rg.get("gain"), " dB")),
        "replaygain_track_peak": _text(rg.get("peak")),
        "replaygain_track_range": _text(_rg_text(rg.get("range"), " dB")),
    }
    data.update({k: v for k, v in _credit_map(meta).items() if v})
    return {k: v for k, v in data.items() if v}


def _rg_text(value, suffix: str = "") -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return _text(value)


def embed(
    audio: Path,
    meta: dict,
    cover: bytes | None = None,
    lyric: str = "",
    translation: str = "",
    progress: ProgressFn = None,
) -> dict:
    """把 meta / 封面 / 歌词写入 audio 文件（原地修改）。

    Returns: {"ok": True, "format": 容器名, "fields": 已写字段列表}
    """
    _mutagen()
    suffix = audio.suffix.lower()
    writers = {
        ".flac": _write_flac,
        ".ogg": _write_ogg,
        ".mp3": _write_mp3,
        ".m4a": _write_m4a,
    }
    writer = writers.get(suffix)
    if writer is None:
        raise TaggingError(f"暂不支持把标签写入 {suffix or '未知'} 格式")
    if not audio.exists() or audio.stat().st_size == 0:
        raise TaggingError("音频文件不存在或为空")

    _report(progress, 0.1)
    fields = writer(str(audio), meta, cover, lyric, translation)
    _report(progress, 1.0)
    return {"ok": True, "format": suffix.lstrip("."), "fields": fields}


def embed_bytes(
    data: bytes,
    ext: str,
    meta: dict,
    cover: bytes | None = None,
    lyric: str = "",
    translation: str = "",
    progress: ProgressFn = None,
) -> dict:
    """内存版 embed：音频数据全程在内存里写标签，不产生临时文件。

    Returns: {"ok": True, "format": 容器名, "fields": [...], "audio": 成品字节}
    """
    _mutagen()
    suffix = ("." + str(ext or "").lstrip(".")).lower()
    writers = {
        ".flac": _write_flac,
        ".ogg": _write_ogg,
        ".mp3": _write_mp3,
        ".m4a": _write_m4a,
    }
    writer = writers.get(suffix)
    if writer is None:
        raise TaggingError(f"暂不支持把标签写入 {suffix or '未知'} 格式")
    if not data:
        raise TaggingError("音频数据为空")

    _report(progress, 0.1)
    handle = io.BytesIO(bytes(data))
    fields = writer(handle, meta, cover, lyric, translation)
    _report(progress, 1.0)
    return {"ok": True, "format": suffix.lstrip("."), "fields": fields, "audio": handle.getvalue()}


def _write_flac(audio: Any, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.flac import FLAC, Picture  # noqa: PLC0415

    data = _canonical(meta)
    f = FLAC(audio)
    written: list[str] = []
    for key, value in data.items():
        name = VORBIS_ALIASES.get(key, key)
        f[name] = value
        written.append(name)
    if lyric:
        f["lyrics"] = lyric
        written.append("lyrics")
    if translation:
        f["lyric_translation"] = translation
        written.append("lyric_translation")
    if cover:
        picture = Picture()
        picture.type = 3  # Cover (front)
        picture.mime = _image_mime(cover)
        picture.desc = "Cover"
        picture.data = cover
        f.clear_pictures()
        f.add_picture(picture)
        written.append("cover")
    _rewind(audio)
    f.save(audio)
    return written


def _write_mp3(audio: Any, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.id3 import (  # noqa: PLC0415
        APIC,
        COMM,
        Frames,
        ID3,
        TXXX,
        USLT,
    )

    data = _canonical(meta)
    try:
        tags = ID3(audio)
    except Exception:  # noqa: BLE001
        tags = ID3()
    written: list[str] = []
    for key, value in data.items():
        kind, target = ID3_MAP.get(key, ("txxx", key.upper()))
        if kind == "text":
            tags.delall(target)
            frame_cls = Frames.get(target)
            if frame_cls is None:
                continue
            tags.add(frame_cls(encoding=3, text=value))
            written.append(target)
        else:
            tags.delall(f"TXXX:{target}")
            tags.add(TXXX(encoding=3, desc=target, text=value))
            written.append(f"TXXX:{target}")
    comment = _text(meta.get("comment"))
    if comment:
        tags.delall("COMM")
        tags.add(COMM(encoding=3, lang="chi", desc="", text=comment))
        written.append("comment")
    if lyric:
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="chi", desc="", text=lyric))
        written.append("lyrics")
    if translation:
        tags.delall("TXXX:LYRIC_TRANSLATION")
        tags.add(TXXX(encoding=3, desc="LYRIC_TRANSLATION", text=translation))
        written.append("lyric_translation")
    if cover:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=_image_mime(cover), type=3, desc="Cover", data=cover))
        written.append("cover")
    _rewind(audio)
    tags.save(audio, v2_version=3)
    return written


def _write_ogg(audio: Any, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.flac import Picture  # noqa: PLC0415
    from mutagen.oggvorbis import OggVorbis  # noqa: PLC0415

    data = _canonical(meta)
    f = OggVorbis(audio)
    written: list[str] = []
    for key, value in data.items():
        name = VORBIS_ALIASES.get(key, key)
        f[name] = value
        written.append(name)
    if lyric:
        f["lyrics"] = lyric
        written.append("lyrics")
    if translation:
        f["lyric_translation"] = translation
        written.append("lyric_translation")
    if cover:
        picture = Picture()
        picture.type = 3
        picture.mime = _image_mime(cover)
        picture.desc = "Cover"
        picture.data = cover
        f["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
        written.append("cover")
    _rewind(audio)
    f.save(audio)
    return written


def _write_m4a(audio: Any, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm  # noqa: PLC0415

    data = _canonical(meta)
    f = MP4(audio)
    written: list[str] = []
    for key, value in data.items():
        if key in ("track_no", "disc_no"):
            numbers = value.split("/")
            if numbers and numbers[0].isdigit():
                total = int(numbers[1]) if len(numbers) > 1 and numbers[1].isdigit() else 0
                # mutagen 的 trkn/disk 渲染要求 (序号, 总数) 二元组
                f[M4A_MAP[key]] = [(int(numbers[0]), total)]
                written.append(M4A_MAP[key])
            continue
        if key == "bpm":
            if value.isdigit():
                f["tmpo"] = [int(value)]
                written.append("tmpo")
            continue
        if key in M4A_MAP and M4A_MAP[key]:
            f[M4A_MAP[key]] = [value]
            written.append(M4A_MAP[key])
        else:
            name = f"----:com.apple.iTunes:{key.upper()}"
            f[name] = [MP4FreeForm(value.encode("utf-8"))]
            written.append(name)
    comment = _text(meta.get("comment"))
    if comment:
        f["\xa9cmt"] = [comment]
        written.append("comment")
    if lyric:
        f["\xa9lyr"] = [lyric]
        written.append("lyrics")
    if translation:
        f["----:com.apple.iTunes:LYRIC_TRANSLATION"] = [MP4FreeForm(translation.encode("utf-8"))]
        written.append("lyric_translation")
    if cover:
        fmt = MP4Cover.FORMAT_PNG if _image_mime(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        f["covr"] = [MP4Cover(cover, imageformat=fmt)]
        written.append("cover")
    _rewind(audio)
    f.save(audio)
    return written

