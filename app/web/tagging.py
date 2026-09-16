"""把元数据（标题 / 歌手 / 专辑 / 封面 / 歌词）嵌入音频文件。

只写标签，不重新编码音频，音质与原文件完全一致。
支持 FLAC、OGG Vorbis、MP3、M4A 四种容器；标签库使用内置的 mutagen（vendor 目录）。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[float], None] | None


class TaggingError(Exception):
    """标签写入失败。"""


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
    fields = writer(audio, meta, cover, lyric, translation)
    _report(progress, 1.0)
    return {"ok": True, "format": suffix.lstrip("."), "fields": fields}


def _common_fields(meta: dict) -> dict:
    return {
        "title": _text(meta.get("title") or meta.get("name")),
        "artist": _text(meta.get("artist") or meta.get("singer")),
        "album": _text(meta.get("album")),
        "albumartist": _text(meta.get("album_artist") or meta.get("artist") or meta.get("singer")),
        "date": _text(meta.get("year") or meta.get("date")),
        "genre": _text(meta.get("genre")),
        "comment": _text(meta.get("comment")),
        "songmid": _text(meta.get("songmid")),
        "songid": _text(meta.get("songid")),
    }


def _write_flac(audio: Path, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.flac import FLAC, Picture  # noqa: PLC0415

    data = _common_fields(meta)
    f = FLAC(str(audio))
    written: list[str] = []
    for key, value in data.items():
        if value:
            f[key] = value
            written.append(key)
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
    f.save()
    return written


def _write_mp3(audio: Path, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.id3 import (  # noqa: PLC0415
        APIC,
        COMM,
        ID3,
        TALB,
        TCON,
        TDRC,
        TIT2,
        TPE1,
        TPE2,
        TXXX,
        USLT,
    )

    data = _common_fields(meta)
    try:
        tags = ID3(str(audio))
    except Exception:  # noqa: BLE001
        tags = ID3()
    written: list[str] = []
    mapping = {
        "title": (TIT2, "title"),
        "artist": (TPE1, "artist"),
        "albumartist": (TPE2, "albumartist"),
        "album": (TALB, "album"),
        "date": (TDRC, "date"),
        "genre": (TCON, "genre"),
    }
    for key, (frame_cls, label) in mapping.items():
        value = data.get(key)
        if value:
            tags.delall(frame_cls.__name__)
            tags.add(frame_cls(encoding=3, text=value))
            written.append(label)
    if data["comment"]:
        tags.delall("COMM")
        tags.add(COMM(encoding=3, lang="chi", desc="", text=data["comment"]))
        written.append("comment")
    if data["songmid"]:
        tags.delall("TXXX:QQMUSIC_SONGMID")
        tags.add(TXXX(encoding=3, desc="QQMUSIC_SONGMID", text=data["songmid"]))
        written.append("songmid")
    if data["songid"]:
        tags.delall("TXXX:QQMUSIC_SONGID")
        tags.add(TXXX(encoding=3, desc="QQMUSIC_SONGID", text=data["songid"]))
        written.append("songid")
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
    tags.save(str(audio), v2_version=3)
    return written


def _write_ogg(audio: Path, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.flac import Picture  # noqa: PLC0415
    from mutagen.oggvorbis import OggVorbis  # noqa: PLC0415

    data = _common_fields(meta)
    f = OggVorbis(str(audio))
    written: list[str] = []
    for key, value in data.items():
        if value:
            f[key] = value
            written.append(key)
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
    f.save()
    return written


def _write_m4a(audio: Path, meta: dict, cover: bytes | None, lyric: str, translation: str) -> list[str]:
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm  # noqa: PLC0415

    data = _common_fields(meta)
    f = MP4(str(audio))
    written: list[str] = []
    mapping = {
        "\xa9nam": data["title"],
        "\xa9ART": data["artist"],
        "aART": data["albumartist"],
        "\xa9alb": data["album"],
        "\xa9day": data["date"],
        "\xa9gen": data["genre"],
    }
    for key, value in mapping.items():
        if value:
            f[key] = [value]
            written.append(key)
    if data["songmid"]:
        f["----:com.apple.iTunes:QQMUSIC_SONGMID"] = [MP4FreeForm(data["songmid"].encode("utf-8"))]
        written.append("songmid")
    if data["songid"]:
        f["----:com.apple.iTunes:QQMUSIC_SONGID"] = [MP4FreeForm(data["songid"].encode("utf-8"))]
        written.append("songid")
    if data["comment"]:
        f["\xa9cmt"] = [data["comment"]]
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
    f.save()
    return written


def load_cover(path: str | Path | None) -> bytes | None:
    """读取封面文件内容，失败返回 None（不阻断下载任务）。"""
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return data if len(data) > 100 else None
