#!/usr/bin/env python3
"""校验 fnpack 产物（.fpk）内容是否符合预期。

用途：在 CI 打包后断言产物结构完整，且生命周期脚本带可执行位。
Windows 工作树没有可执行位，因此本地 Windows 构建的 fpk 会因本脚本报错，
必须使用 Linux/CI 构建的产物（脚本内会给出明确提示）。

用法：python tools/ci/inspect_fpk.py qqmusic-downloader.fpk
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path

REQUIRED_FILES = (
    "manifest",
    "ICON.PNG",
    "ICON_256.PNG",
    "config/privilege",
    "config/resource",
    "wizard/install",
    "wizard/config",
    "cmd/main",
    "cmd/install_init",
    "cmd/install_callback",
    "cmd/config_init",
    "cmd/config_callback",
    "cmd/uninstall_init",
    "cmd/uninstall_callback",
    "cmd/upgrade_init",
    "cmd/upgrade_callback",
    "app.tgz",
)

EXECUTABLE_REQUIRED = (
    "cmd/main",
    "cmd/install_init",
    "cmd/install_callback",
    "cmd/config_init",
    "cmd/config_callback",
    "cmd/uninstall_init",
    "cmd/uninstall_callback",
    "cmd/upgrade_init",
    "cmd/upgrade_callback",
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：python tools/ci/inspect_fpk.py <xx.fpk>")
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"FAIL 未找到产物：{path}")
        return 1

    errors: list[str] = []
    with tarfile.open(path, "r") as tar:
        members = {m.name.lstrip("./"): m for m in tar.getmembers()}

        for name in REQUIRED_FILES:
            if name not in members:
                errors.append(f"缺少文件：{name}")

        for name in EXECUTABLE_REQUIRED:
            member = members.get(name)
            if member is None:
                continue
            if not (member.mode & 0o111):
                errors.append(
                    f"{name} 无可执行位（mode={oct(member.mode)}）。"
                    "Windows 构建的产物不可直接安装，请使用 CI 构建的 .fpk"
                )

        inner = members.get("app.tgz")
        if inner is not None:
            import io

            data = tar.extractfile(inner)
            if data is None:
                errors.append("app.tgz 无法读取")
            else:
                import io as _io

                with tarfile.open(fileobj=_io.BytesIO(data.read()), mode="r:gz") as t2:
                    names = {n.lstrip("./") for n in t2.getnames()}
                    cfg_member = next(
                        (n for n in t2.getmembers() if n.name.lstrip("./") == "ui/config"), None
                    )
                    cfg_raw = (
                        t2.extractfile(cfg_member).read().decode("utf-8", "replace")
                        if cfg_member is not None
                        else None
                    )
                for name in ("web/main.py", "ui/index.html", "ui/app.js", "requirements.txt"):
                    if not any(n.endswith(name) for n in names):
                        errors.append(f"app.tgz 中缺少 {name}")

                # C 加速解密库必须在包内（否则 NAS 上退回纯 Python，解密慢 30 倍以上）。
                # 由 CI 的「编译 QMC2 C 加速库」步骤在 fnpack build 之前生成。
                if not any(n.endswith("web/libqmc2_fast.so") for n in names):
                    errors.append(
                        "app.tgz 中缺少 web/libqmc2_fast.so（C 加速解密库未随包分发）"
                    )

                # 入口 url 必须以 / 结尾：否则飞牛网关会把 /app/<name> 307 重定向到
                # 丢失端口的地址（IP:端口 访问时掉到 80 端口），桌面窗口直接空白。
                if cfg_raw is None:
                    errors.append("app.tgz 中缺少 ui/config")
                else:
                    import json as _json

                    try:
                        entries = _json.loads(cfg_raw).get(".url", {})
                    except ValueError as exc:
                        errors.append(f"ui/config 不是合法 JSON：{exc}")
                    else:
                        for key, entry in entries.items():
                            url = (entry or {}).get("url", "")
                            if not url.endswith("/"):
                                errors.append(
                                    f"ui/config 入口 {key} 的 url 必须以 / 结尾，当前为 {url!r}"
                                    "（否则网关 307 会丢掉端口，桌面窗口空白）"
                                )

    if errors:
        for item in errors:
            print(f"FAIL {item}")
        return 1
    print(f"OK {path.name} 结构完整，生命周期脚本可执行，app.tgz 内容齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
