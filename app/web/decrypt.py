"""QQ 音乐加密文件解密（QMC2：MapCipher / RC4Cipher + Tencent TEA 密钥派生）。

移植自 qmdec（MIT License）：crypto.py / map_cipher.py / rc4.py / musicex.py。
纯 Python 实现可在任意平台运行；若随包提供 C 加速库（csrc/qmc2_fast.c 编译出的
libqmc2_fast.so）则自动启用（约 39 倍加速），缺失或不兼容时静默回退。

术语：
- ekey：接口下发的加密资源密钥（base64），需经 TEA 派生为最终密钥；
- 加密文件结构：`[加密音频数据][musicex 尾部]` 或旧式 `[加密音频数据][ekey][QTag/STag]`；
- 解密只做逐字节异或，不重采样、不改编码，音质完全不变。
"""

from __future__ import annotations

import base64
import math
import struct
from typing import Callable

try:  # qmdec 的 C 加速（libqmc2_fast）：不可用时回退纯 Python
    from .qmc_fast import decrypt as _fast_decrypt
except Exception:  # noqa: BLE001
    try:
        from qmc_fast import decrypt as _fast_decrypt  # type: ignore
    except Exception:  # noqa: BLE001
        def _fast_decrypt(key: bytes, buf: bytearray, offset: int) -> bool:  # type: ignore[misc]
            return False

MUSICEX_MAGIC = b"musicex\x00"
QTAG_MAGIC = b"QTag"
STAG_MAGIC = b"STag"

ENCV2_PREFIX = b"QQMusic EncV2,Key:"
ENCV2_KEY1 = bytes((0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40,
                    0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28))
ENCV2_KEY2 = bytes((0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28, 0x23,
                    0x40, 0x21, 0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59))

# 单次读取块大小（与参考实现一致：10 个 RC4 分段）
CHUNK_SIZE = 5120 * 10

ProgressFn = Callable[[int, int], None] | None


class DecryptError(Exception):
    """解密失败（格式不支持、密钥无效等）。"""


# --------------------------------------------------------------------------
# 加密文件尾部解析
# --------------------------------------------------------------------------
def _parse_musicex(handle, size: int) -> dict | None:
    handle.seek(-16, 2)
    raw = handle.read(4)
    if len(raw) < 4:
        return None
    tail_size = struct.unpack("<I", raw)[0]
    if tail_size <= 0 or tail_size > 4096 or size < 16 + tail_size:
        return None
    handle.seek(-(16 + tail_size), 2)
    tail = handle.read(tail_size)
    song_mid = tail[28:88].decode("utf-16-le", errors="ignore").rstrip("\x00") if len(tail) >= 88 else ""
    filename = tail[88:184].decode("utf-16-le", errors="ignore").rstrip("\x00") if len(tail) >= 184 else ""
    return {
        "format": "musicex",
        "song_mid": song_mid,
        "filename": filename,
        "audio_size": size - 16 - tail_size,
        "ekey": "",
    }


def _parse_legacy(handle, size: int, tag_type: bytes) -> dict | None:
    handle.seek(-8, 2)
    raw = handle.read(4)
    if len(raw) < 4:
        return None
    ekey_len = struct.unpack("<I", raw)[0]
    if ekey_len <= 0 or ekey_len > 4096 or size < 8 + ekey_len:
        return None
    audio_size = size - 8 - ekey_len
    handle.seek(audio_size, 0)
    data = handle.read(ekey_len)
    if tag_type == QTAG_MAGIC:
        parts = data.split(b",")
        song_mid = parts[0].decode("utf-8", errors="ignore") if parts else ""
        ekey = parts[1].decode("utf-8", errors="ignore") if len(parts) > 1 else ""
    else:
        song_mid = ""
        ekey = data.decode("utf-8", errors="ignore")
    return {
        "format": "legacy",
        "song_mid": song_mid,
        "filename": "",
        "audio_size": audio_size,
        "ekey": ekey.strip(),
    }


# --------------------------------------------------------------------------
# 密钥派生（Tencent TEA）
# --------------------------------------------------------------------------
def _simple_make_key(salt: int, length: int) -> bytes:
    buf = bytearray(length)
    for i in range(length):
        buf[i] = int(abs(math.tan(float(salt) + float(i) * 0.1)) * 100.0) & 0xFF
    return bytes(buf)


def _tea_decrypt_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack(">II", block)
    k0, k1, k2, k3 = struct.unpack(">4I", key)
    delta = 0x9E3779B9
    total = (delta * 16) & 0xFFFFFFFF
    for _ in range(16):
        v1 = (v1 - (((v0 << 4) + k2) ^ (v0 + total) ^ ((v0 >> 5) + k3))) & 0xFFFFFFFF
        v0 = (v0 - (((v1 << 4) + k0) ^ (v1 + total) ^ ((v1 >> 5) + k1))) & 0xFFFFFFFF
        total = (total - delta) & 0xFFFFFFFF
    return struct.pack(">II", v0, v1)


def _decrypt_tencent_tea(in_buf: bytes, key: bytes) -> bytes | None:
    if len(in_buf) % 8 != 0 or len(in_buf) < 16:
        return None
    dest_buf = bytearray(_tea_decrypt_block(in_buf[:8], key))
    pad_len = dest_buf[0] & 0x07
    out_len = len(in_buf) - 1 - pad_len - 2 - 7
    if out_len <= 0:
        return None
    out = bytearray(out_len)
    iv_prev, iv_cur = bytes(8), in_buf[:8]
    in_pos, dest_idx = 8, 1 + pad_len
    state = {"dest_buf": dest_buf}

    def crypt_block() -> None:
        nonlocal iv_prev, iv_cur, in_pos, dest_idx
        iv_prev = iv_cur
        iv_cur = in_buf[in_pos : in_pos + 8]
        buf = state["dest_buf"]
        state["dest_buf"] = bytearray(buf[i] ^ in_buf[in_pos + i] for i in range(8))
        state["dest_buf"] = bytearray(_tea_decrypt_block(bytes(state["dest_buf"]), key))
        in_pos += 8
        dest_idx = 0

    i = 0
    while i < 2:
        if dest_idx < 8:
            dest_idx += 1
            i += 1
        else:
            crypt_block()
    out_pos = 0
    while out_pos < out_len:
        if dest_idx < 8:
            out[out_pos] = state["dest_buf"][dest_idx] ^ iv_prev[dest_idx]
            dest_idx += 1
            out_pos += 1
        else:
            crypt_block()
    return bytes(out)


def _decrypt_encv2(raw: bytes) -> bytes | None:
    payload = raw[len(ENCV2_PREFIX):]
    dec1 = _decrypt_tencent_tea(payload, ENCV2_KEY1)
    if dec1 is None:
        return None
    dec2 = _decrypt_tencent_tea(dec1, ENCV2_KEY2)
    if dec2 is None:
        return None
    try:
        return base64.b64decode(dec2)
    except Exception:  # noqa: BLE001
        return None


def derive_key(ekey_b64: str) -> bytes | None:
    """ekey(base64) -> 最终解密密钥。"""
    try:
        raw_key = base64.b64decode(str(ekey_b64).strip())
    except Exception:  # noqa: BLE001
        return None
    if not raw_key:
        return None
    if raw_key[: len(ENCV2_PREFIX)] == ENCV2_PREFIX:
        raw_key = _decrypt_encv2(raw_key)
        if raw_key is None or len(raw_key) < 16:
            return None
    if len(raw_key) < 16:
        return None
    simple_key = _simple_make_key(106, 8)
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i * 2] = simple_key[i]
        tea_key[i * 2 + 1] = raw_key[i]
    rs = _decrypt_tencent_tea(raw_key[8:], bytes(tea_key))
    if rs is None:
        return None
    return raw_key[:8] + rs


# --------------------------------------------------------------------------
# 两种流密码
# --------------------------------------------------------------------------
class MapCipher:
    """短密钥（<=300 字节）使用的映射密码。"""

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.n = len(key)

    @staticmethod
    def _rotate(value: int, bits: int) -> int:
        shift = (bits + 4) % 8
        return ((value << shift) | (value >> shift)) & 0xFF

    def _get_mask(self, offset: int) -> int:
        if self.n == 0:
            return 0
        if offset > 0x7FFF:
            offset %= 0x7FFF
        idx = (offset * offset + 71214) % self.n
        return self._rotate(self.key[idx], idx & 0x07)

    def decrypt(self, buf: bytearray, offset: int) -> None:
        if _fast_decrypt(self.key, buf, offset):
            return
        for i in range(len(buf)):
            buf[i] ^= self._get_mask(offset + i)


class RC4Cipher:
    """长密钥使用的分段 RC4 流密码。"""

    SEGMENT_SIZE = 5120
    FIRST_SEGMENT_SIZE = 128

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.n = len(key)
        self.box = [i & 0xFF for i in range(self.n)]
        j = 0
        for i in range(self.n):
            j = (j + self.box[i] + key[i]) % self.n
            self.box[i], self.box[j] = self.box[j], self.box[i]
        self.hash = self._compute_hash()

    def _compute_hash(self) -> int:
        h = 1
        for v in self.key:
            if v == 0:
                continue
            nh = (h * v) & 0xFFFFFFFF
            if nh == 0 or nh <= h:
                break
            h = nh
        return h

    def _get_segment_skip(self, id_val: int) -> int:
        seed = int(self.key[id_val % self.n])
        if seed == 0:
            return 0
        idx = int(float(self.hash) / float((id_val + 1) * seed) * 100.0)
        return idx % self.n

    def decrypt(self, buf: bytearray, offset: int) -> None:
        if _fast_decrypt(self.key, buf, offset):
            return
        to_process = len(buf)
        processed = 0

        if offset < self.FIRST_SEGMENT_SIZE:
            block_size = min(to_process, self.FIRST_SEGMENT_SIZE - offset)
            self._enc_first_segment(buf, 0, block_size, offset)
            processed += block_size
            offset += block_size
            to_process -= block_size
            if to_process == 0:
                return

        if offset % self.SEGMENT_SIZE != 0:
            block_size = min(to_process, self.SEGMENT_SIZE - offset % self.SEGMENT_SIZE)
            self._enc_a_segment(buf, processed, block_size, offset)
            processed += block_size
            offset += block_size
            to_process -= block_size
            if to_process == 0:
                return

        while to_process > self.SEGMENT_SIZE:
            self._enc_a_segment(buf, processed, self.SEGMENT_SIZE, offset)
            processed += self.SEGMENT_SIZE
            offset += self.SEGMENT_SIZE
            to_process -= self.SEGMENT_SIZE

        if to_process > 0:
            self._enc_a_segment(buf, processed, to_process, offset)

    def _enc_first_segment(self, buf: bytearray, buf_offset: int, length: int, stream_offset: int) -> None:
        for i in range(length):
            skip = self._get_segment_skip(stream_offset + i)
            buf[buf_offset + i] ^= self.key[skip]

    def _enc_a_segment(self, buf: bytearray, buf_offset: int, length: int, stream_offset: int) -> None:
        box_copy = self.box.copy()
        j, k = 0, 0
        skip_len = (stream_offset % self.SEGMENT_SIZE) + self._get_segment_skip(stream_offset // self.SEGMENT_SIZE)

        for i in range(-skip_len, length):
            j = (j + 1) % self.n
            k = (box_copy[j] + k) % self.n
            box_copy[j], box_copy[k] = box_copy[k], box_copy[j]
            if i >= 0:
                idx = (box_copy[j] + box_copy[k]) % self.n
                buf[buf_offset + i] ^= box_copy[idx]


def make_cipher(key: bytes):
    return RC4Cipher(key) if len(key) > 300 else MapCipher(key)


SIGNATURES = (
    (b"fLaC", ".flac"),
    (b"OggS", ".ogg"),
    (b"ID3", ".mp3"),
    (b"ftyp", ".m4a"),
)


def sniff_ext(data: bytes) -> str:
    """从文件头识别真实容器格式。"""
    if data[:4] == b"fLaC":
        return ".flac"
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return ".mp3"
    if data[4:8] == b"ftyp":
        return ".m4a"
    return ".bin"


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
ENCRYPTED_SUFFIXES = (
    ".mflac",
    ".mflac0",
    ".mgg",
    ".mgg1",
    ".mggl",
    ".qmc0",
    ".qmc2",
    ".qmc3",
    ".qmcflac",
    ".qmcogg",
)


# --------------------------------------------------------------------------
# 内存解密（半成品不落盘：中间数据全部在内存流转，只有成品写盘）
# --------------------------------------------------------------------------
def parse_tail_bytes(data: bytes) -> dict | None:
    """bytes 版尾部解析。返回 None 表示文件未加密。"""
    size = len(data)
    if size < 16:
        return None
    if data[-8:] == MUSICEX_MAGIC:
        raw = data[-16:-12]
        if len(raw) < 4:
            return None
        tail_size = struct.unpack("<I", raw)[0]
        if tail_size <= 0 or tail_size > 4096 or size < 16 + tail_size:
            return None
        tail = data[-(16 + tail_size):-16]
        song_mid = tail[28:88].decode("utf-16-le", errors="ignore").rstrip("\x00") if len(tail) >= 88 else ""
        filename = tail[88:184].decode("utf-16-le", errors="ignore").rstrip("\x00") if len(tail) >= 184 else ""
        return {
            "format": "musicex",
            "song_mid": song_mid,
            "filename": filename,
            "audio_size": size - 16 - tail_size,
            "ekey": "",
        }
    tail4 = data[-4:]
    if tail4 in (QTAG_MAGIC, STAG_MAGIC):
        raw = data[-8:-4]
        if len(raw) < 4:
            return None
        ekey_len = struct.unpack("<I", raw)[0]
        if ekey_len <= 0 or ekey_len > 4096 or size < 8 + ekey_len:
            return None
        audio_size = size - 8 - ekey_len
        blob = data[audio_size:audio_size + ekey_len]
        if tail4 == QTAG_MAGIC:
            parts = blob.split(b",")
            song_mid = parts[0].decode("utf-8", errors="ignore") if parts else ""
            ekey = parts[1].decode("utf-8", errors="ignore") if len(parts) > 1 else ""
        else:
            song_mid = ""
            ekey = blob.decode("utf-8", errors="ignore")
        return {
            "format": "legacy",
            "song_mid": song_mid,
            "filename": "",
            "audio_size": audio_size,
            "ekey": ekey.strip(),
        }
    return None


def decrypt_buffer(buf: bytearray, key: bytes, progress: ProgressFn = None) -> str:
    """原地解密内存缓冲，返回实际使用的密码名（map / rc4）。"""
    cipher = make_cipher(key)
    total = len(buf)
    done = 0
    while done < total:
        size = min(CHUNK_SIZE, total - done)
        chunk = buf[done:done + size]
        cipher.decrypt(chunk, done)
        buf[done:done + size] = chunk
        done += size
        if progress:
            progress(done, total)
    return "rc4" if isinstance(cipher, RC4Cipher) else "map"


def decrypt_bytes(
    data: bytes,
    ekey_b64: str = "",
    progress: ProgressFn = None,
    encrypted_hint: bool = False,
    suffix: str = "",
) -> dict:
    """内存解密：不产生任何中间文件。

    返回 {"encrypted": bool, "ext": 输出扩展名, "audio": 解密后字节,
          "audio_size": 音频字节数, "cipher": map/rc4/plain}。
    """
    audio = bytes(data)
    size = len(audio)
    if size <= 0:
        raise DecryptError("原始音频为空，请重试")

    tail = parse_tail_bytes(audio)
    ekey = (ekey_b64 or "").strip()

    if tail is None:
        looks_encrypted = bool(encrypted_hint or (suffix or "").lower() in ENCRYPTED_SUFFIXES)
        if not looks_encrypted:
            ext = sniff_ext(audio[:16])
            if ext == ".bin":
                ext = suffix or ".bin"
            if progress:
                progress(size, size)
            return {"encrypted": False, "ext": ext, "audio": audio, "audio_size": size, "cipher": "plain"}
        if not ekey:
            raise DecryptError("缺少解密密钥（ekey），请重新登录后再试")
        final_key = derive_key(ekey)
        if not final_key:
            raise DecryptError("解密密钥无效，请重新登录后再试")
        out = bytearray(audio)
        cipher_name = decrypt_buffer(out, final_key, progress)
        ext = sniff_ext(bytes(out[:16]))
        if ext == ".bin":
            ext = sniff_ext(audio[:16])
            if ext == ".bin":
                raise DecryptError("解密结果无法识别为音频格式（密钥可能不正确）")
        return {"encrypted": True, "ext": ext, "audio": bytes(out), "audio_size": size, "cipher": cipher_name}

    audio_size = int(tail.get("audio_size") or 0)
    if audio_size <= 0 or audio_size > size:
        raise DecryptError("加密文件尾部异常，无法定位音频数据")

    ekey = (ekey_b64 or tail.get("ekey") or "").strip()
    if not ekey:
        raise DecryptError("缺少解密密钥（ekey），请重新登录后再试")
    final_key = derive_key(ekey)
    if not final_key:
        raise DecryptError("解密密钥无效，请重新登录后再试")

    out = bytearray(audio[:audio_size])
    cipher_name = decrypt_buffer(out, final_key, progress)
    ext = sniff_ext(bytes(out[:16]))
    if ext == ".bin":
        ext = sniff_ext(audio[:16])
        if ext == ".bin":
            raise DecryptError("解密结果无法识别为音频格式（密钥可能不正确）")
    return {"encrypted": True, "ext": ext, "audio": bytes(out), "audio_size": audio_size, "cipher": cipher_name}
