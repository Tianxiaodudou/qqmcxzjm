"""fnOS 项目结构检查（CI 预检，替代本地手工核对）。"""

import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
errors = []
warns = []


def need(path, kind="file"):
    p = os.path.join(ROOT, path)
    if kind == "file" and not os.path.isfile(p):
        errors.append(f"缺少文件: {path}")
    if kind == "dir" and not os.path.isdir(p):
        errors.append(f"缺少目录: {path}")
    return p


def load_json(path, required=True):
    p = os.path.join(ROOT, path)
    if not os.path.isfile(p):
        if required:
            errors.append(f"缺少 JSON: {path}")
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"JSON 解析失败 {path}: {exc}")
        return None


def main():
    for p in ("manifest", "ICON.PNG", "ICON_256.PNG", "config/privilege", "config/resource"):
        need(p)
    for d in ("app", "cmd", "wizard"):
        need(d, "dir")

    manifest_path = os.path.join(ROOT, "manifest")
    if os.path.isfile(manifest_path):
        text = open(manifest_path, encoding="utf-8").read()
        for field in ("appname", "version", "display_name", "desktop_uidir"):
            if not re.search(rf"^{field}\s*=\s*\S", text, re.M):
                errors.append(f"manifest 缺少字段: {field}")
        m = re.search(r"^desktop_uidir\s*=\s*(\S+)", text, re.M)
        if m:
            need(os.path.join("app", m.group(1)), "dir")

    load_json("config/privilege")
    load_json("config/resource")
    for extra in ("wizard/config", "wizard/install", "app/ui/config"):
        p = os.path.join(ROOT, extra)
        if os.path.isfile(p):
            load_json(extra, required=False)
        else:
            warns.append(f"可选文件不存在: {extra}")

    index = os.path.join(ROOT, "app", "ui", "index.html")
    if os.path.isfile(index):
        html = open(index, encoding="utf-8").read()
        ui_dir = os.path.dirname(index)
        for ref in re.findall(r'(?:href|src)="([^"]+)"', html):
            if ref.startswith(("http", "//", "#", "data:")):
                continue
            # 页面是通过 /static 或 /<网关前缀>/static 提供的，去掉该前缀后对应 ui 目录中的文件
            rel = ref.split("?")[0]
            if rel.startswith("static/"):
                rel = rel[len("static/"):]
            if not os.path.exists(os.path.join(ui_dir, rel)):
                errors.append(f"index.html 引用的资源不存在: {ref}")
    else:
        warns.append("未找到 app/ui/index.html（命令行构建可忽略）")

    for rel in sorted(os.listdir(os.path.join(ROOT, "cmd"))):
        p = os.path.join(ROOT, "cmd", rel)
        if os.path.isfile(p) and not os.access(p, os.X_OK):
            warns.append(f"cmd/{rel} 无执行权限（构建前请 chmod +x）")

    for w in warns:
        print(f"WARN {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"CHECK RESULT errors={len(errors)} warnings={len(warns)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
