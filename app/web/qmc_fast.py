"""qmdec C 加速库（libqmc2_fast）加载器。

对应上游项目 qmdec 的 `qmc2_fast.c`：把 QMC2 的 MapCipher / RC4Cipher 用 C 实现，
实测比纯 Python 快约 39 倍。本模块只负责「按需加载 + 安全回退」：

- 库文件随安装包预编译（CI 里 gcc -O2 -shared -fPIC 生成，Windows/mac 亦可本地编译）；
- 任何异常（文件缺失、架构不符、符号缺失）都只记录一次日志，然后永久回退纯 Python；
- 对外只暴露三个函数：available() / info() / decrypt()。
"""

from __future__ import annotations

import ctypes
import logging
import threading
from pathlib import Path

logger = logging.getLogger("qqmusic.crypto")

LIB_NAMES = (
    "libqmc2_fast.so",
    "qmc2_fast.so",
    "libqmc2_fast.dylib",
    "qmc2_fast.dll",
)
LIB_DIRS = (
    Path(__file__).resolve().parent / "lib",
    Path(__file__).resolve().parent,
)

_lock = threading.Lock()
_lib: ctypes.CDLL | None = None
_path: str = ""
_failed = False


def _try_load() -> ctypes.CDLL | None:
    for folder in LIB_DIRS:
        for name in LIB_NAMES:
            candidate = folder / name
            if not candidate.is_file():
                continue
            try:
                lib = ctypes.CDLL(str(candidate))
                func = lib.qmc2_decrypt
                func.argtypes = [
                    ctypes.POINTER(ctypes.c_ubyte),
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_ubyte),
                    ctypes.c_int,
                    ctypes.c_int,
                ]
                func.restype = None
            except Exception as exc:  # noqa: BLE001
                logger.info("C 加速库 %s 加载失败，回退纯 Python：%s", candidate.name, exc)
                continue
            return lib
    return None


def _get() -> ctypes.CDLL | None:
    """惰性加载；失败后不再重试（避免每个音频块都去 stat 一次文件系统）。"""
    global _lib, _path, _failed
    if _lib is not None or _failed:
        return _lib
    with _lock:
        if _lib is not None or _failed:
            return _lib
        lib = _try_load()
        if lib is None:
            _failed = True
            return None
        _lib = lib
        for folder in LIB_DIRS:
            for name in LIB_NAMES:
                candidate = folder / name
                if candidate.is_file():
                    _path = str(candidate)
                    break
            if _path:
                break
        logger.info("已启用 C 加速解密：%s", _path or "libqmc2_fast")
        return _lib


def available() -> bool:
    """C 加速库是否可用。"""
    return _get() is not None


def info() -> dict:
    """给前端/日志用的状态信息。"""
    lib = _get()
    return {"available": lib is not None, "path": _path if lib is not None else ""}


def decrypt(key: bytes, buf: bytearray, offset: int) -> bool:
    """用 C 库原地解密 buf（offset 为 buf 首字节在音频流中的绝对偏移）。

    返回 True 表示已完成；False 表示调用方需要走纯 Python 实现。
    """
    if not buf:
        return True
    lib = _get()
    if lib is None:
        return False
    try:
        key_buf = (ctypes.c_ubyte * len(key)).from_buffer_copy(key) if key else (ctypes.c_ubyte * 0)()
        data_buf = (ctypes.c_ubyte * len(buf)).from_buffer(buf)
        lib.qmc2_decrypt(key_buf, len(key), data_buf, len(buf), int(offset))
    except Exception as exc:  # noqa: BLE001
        logger.info("C 加速解密失败，本次回退纯 Python：%s", exc)
        return False
    return True
