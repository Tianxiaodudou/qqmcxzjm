#!/usr/bin/env python3
"""验证 C 加速库与纯 Python 解密逐字节等价。

背景：`app/web/decrypt.py` 在检测到 `libqmc2_fast.so`（由 `app/web/csrc/qmc2_fast.c`
编译而来）时走 C 路径，缺失则静默回退纯 Python。C 路径一旦与 Python 语义有偏差，
解密出的音频就是错的（不是崩溃，而是静默损坏），因此必须逐字节比对。

做法：本脚本先用系统编译器把 C 源码编译成共享库，再针对 MapCipher（短密钥）与
RC4Cipher（长密钥）在多种「块长度 × 流偏移」组合下，分别用 C 路径和纯 Python 路径
解密同一段数据，断言结果完全一致；最后做一次「构造加密数据 → C 路径解密 → 还原」
的端到端比对。

用法：python tools/ci/test_c_accel.py
无可用编译器时打印 SKIP 并以 0 退出（本地开发机常见）；CI（ubuntu）装有 gcc，必须真跑。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "app" / "web"
SRC = WEB / "csrc" / "qmc2_fast.c"

IS_WINDOWS = os.name == "nt"
LIB_NAME = "qmc2_fast.dll" if IS_WINDOWS else "libqmc2_fast.so"


def find_compiler() -> list[str] | None:
    """返回编译命令前缀（列表），找不到编译器则返回 None。"""
    for cand in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if not cand:
            continue
        path = shutil.which(cand)
        if path:
            return [path]
    zig = shutil.which("zig")
    if zig:
        return [zig, "cc"]
    try:  # pip install ziglang 的环境（无 PATH 上的 zig，但可 python -m ziglang cc）
        import ziglang  # noqa: F401

        return [sys.executable, "-m", "ziglang", "cc"]
    except Exception:  # noqa: BLE001
        pass
    return None


def compile_lib(cc: list[str]) -> Path:
    out = WEB / LIB_NAME
    if out.exists():
        out.unlink()
    cmd = list(cc) + [
        "-O2",
        "-shared",
        "-fPIC",
        "-o",
        str(out),
        str(SRC),
    ]
    print("CC:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:])
        raise SystemExit("FAIL 编译 C 加速库失败")
    if not out.exists():
        raise SystemExit("FAIL 编译命令成功但产物不存在：%s" % out)
    print("编译产物：%s (%d 字节)" % (out.name, out.stat().st_size))
    return out


def main() -> int:
    if not SRC.is_file():
        print("FAIL 缺少 C 源码：%s" % SRC)
        return 1

    cc = find_compiler()
    if cc is None:
        print("SKIP 未找到 C 编译器（cc/gcc/clang/zig），跳过 C 加速等价性校验")
        return 0

    out = compile_lib(cc)

    sys.path.insert(0, str(WEB))
    import qmc_fast  # noqa: E402

    if not qmc_fast.available():
        print("FAIL 已编译出 %s 但 qmc_fast 未能加载" % out.name)
        print("     期望路径：%s" % out)
        return 1
    info = qmc_fast.info()
    print("C 加速库已加载：", info)

    import decrypt  # noqa: E402

    fast_decrypt = decrypt._fast_decrypt

    def pure(mod, key: bytes, buf: bytearray, offset: int) -> None:  # noqa: ANN001
        """强制走纯 Python 路径（临时屏蔽 C 路径）。"""
        mod._fast_decrypt = lambda *a, **k: False
        try:
            mod.make_cipher(key).decrypt(buf, offset)
        finally:
            mod._fast_decrypt = fast_decrypt

    def fast(key: bytes, buf: bytearray, offset: int) -> None:
        decrypt._fast_decrypt = fast_decrypt  # 确保走 C 路径（pure 会临时把它换成空实现）
        decrypt.make_cipher(key).decrypt(buf, offset)

    cases = 0
    bad: list[str] = []

    # 密钥：短（MapCipher）与长（RC4Cipher，>300 字节且含 0 字节）
    keys = {
        "map-short": bytes((i * 7 + 13) & 0xFF for i in range(32)),
        "map-300": bytes((i * 11 + 3) & 0xFF for i in range(300)),
        "rc4-301": bytes((i * 5 + 1) & 0xFF for i in range(301)),
        "rc4-512-with-zeros": bytes((0 if i % 37 == 0 else (i * 3 + 7) & 0xFF) for i in range(512)),
        "rc4-4096": bytes((0 if i % 101 == 0 else (i * 17 + 5) & 0xFF) for i in range(4096)),
    }
    sizes = (1, 7, 128, 200, 5119, 5120, 5121, 10240, 20000, 51200, 51201)
    offsets = (0, 1, 127, 128, 129, 5119, 5120, 5121, 32767, 32768, 32769, 100000)

    for key_name, key in keys.items():
        for size in sizes:
            for offset in offsets:
                data = bytes((i * 29 + 11) & 0xFF for i in range(size))
                a = bytearray(data)
                ref = bytearray(data)
                fast(key, a, offset)
                pure(decrypt, key, ref, offset)
                cases += 1
                if bytes(a) != bytes(ref):
                    diff = next(i for i in range(size) if a[i] != ref[i])
                    bad.append("%s size=%d offset=%d 首个差异@%d" % (key_name, size, offset, diff))

    # 端到端：把「密钥流」异或到明文上伪造加密数据，再交给 C 路径还原
    for key_name, key in keys.items():
        size = 60000
        plain = bytes((i * 31 + 17) & 0xFF for i in range(size))
        keystream = bytearray(size)
        pure(decrypt, key, keystream, 0)  # 0 ^ ks = ks
        enc = bytearray(p ^ k for p, k in zip(plain, keystream))
        fast(key, enc, 0)
        cases += 1
        if bytes(enc) != plain:
            idx = next(i for i in range(size) if enc[i] != plain[i])
            bad.append("e2e-%s 首个差异@%d" % (key_name, idx))

    # 分块解密（模拟下载器的流式调用）也必须与一次性解密一致
    for key_name, key in keys.items():
        size = 130000
        data = bytes((i * 13 + 9) & 0xFF for i in range(size))
        whole = bytearray(data)
        fast(key, whole, 0)
        chunked = bytearray(data)
        step = 5120 * 3
        pos = 0
        while pos < size:
            piece = chunked[pos : pos + step]
            fast(key, piece, pos)
            chunked[pos : pos + step] = piece
            pos += step
        cases += 1
        if bytes(whole) != bytes(chunked):
            idx = next(i for i in range(size) if whole[i] != chunked[i])
            bad.append("chunked-%s 首个差异@%d" % (key_name, idx))

    # 顺带报到 C vs Python 的吞吐（不参与判定，仅供日志参考）
    key = keys["rc4-512-with-zeros"]
    buf = bytearray(4 * 1024 * 1024)
    t0 = time.perf_counter()
    fast(key, buf, 0)
    t1 = time.perf_counter()
    buf = bytearray(4 * 1024 * 1024)
    pure(decrypt, key, buf, 0)
    t2 = time.perf_counter()
    if t1 > t0 and t2 > t1:
        print("吞吐参考：C %.1f MB/s，纯 Python %.1f MB/s，加速 %.1f 倍"
              % (4 / (t1 - t0), 4 / (t2 - t1), (t2 - t1) / (t1 - t0)))

    if bad:
        for item in bad[:20]:
            print("FAIL", item)
        print("C-ACCEL RESULT cases=%d fail=%d" % (cases, len(bad)))
        return 1
    print("C-ACCEL RESULT cases=%d fail=0（C 路径与纯 Python 逐字节一致）" % cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
