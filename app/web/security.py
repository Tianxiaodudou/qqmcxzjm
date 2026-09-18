"""安全工具：日志脱敏、文件名清洗、路径校验。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import env

_SENSITIVE_KEYS = (
    "musickey",
    "musicid",
    "refresh_token",
    "refresh_key",
    "access_token",
    "str_musicid",
    "encrypt_uin",
    "qqmusic_key",
    "cookie",
)

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def mask(value: str | None) -> str:
    """对敏感字符串脱敏，只保留首尾少量字符。"""
    if not value:
        return ""
    text = str(value)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}***{text[-4:]}"


def sanitize_log(text: str) -> str:
    """日志脱敏：把可能出现的凭证字段替换为掩码。"""
    if not text:
        return ""
    result = str(text)
    for key in _SENSITIVE_KEYS:
        result = re.sub(
            rf'("{key}"\s*:\s*")([^"]{{1,200}})(")',
            lambda m: m.group(1) + mask(m.group(2)) + m.group(3),
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            rf"({key}=)([^&;\s\"']+)",
            lambda m: m.group(1) + mask(m.group(2)),
            result,
            flags=re.IGNORECASE,
        )
    return result


def sanitize_filename(name: str, fallback: str = "unnamed", max_length: int = 80) -> str:
    """清洗文件名，移除路径分隔符与非法字符，避免目录穿越。"""
    text = unicodedata.normalize("NFC", str(name or "")).strip()
    text = _ILLEGAL_CHARS.sub("_", text)
    text = text.replace("..", "_").strip(". ")
    if not text:
        text = fallback
    if text.upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    if len(text) > max_length:
        text = text[:max_length].rstrip(". ")
    return text or fallback


def is_authorized_dir(path: Path, extra: list[str] | None = None) -> bool:
    """判断目录是否位于授权范围内。

    extra 为运行时查询到的授权目录（飞牛 trim.file.userAccess / sharedAccess），
    与启动时注入的 env.AUTHORIZED_PATHS 取并集。
    """
    try:
        target = path.resolve()
    except OSError:
        return False
    candidates = [raw for raw in env.AUTHORIZED_PATHS if raw]
    candidates += [raw for raw in (extra or []) if raw]
    if not candidates:
        return False
    for raw in candidates:
        try:
            allowed = Path(raw).resolve()
        except OSError:
            continue
        if target == allowed or allowed in target.parents:
            return True
    return False


def file_fingerprint(path: Path) -> str:
    """文件指纹（大小 + 修改时间），用于历史检查缓存。"""
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_size}:{int(stat.st_mtime)}"
