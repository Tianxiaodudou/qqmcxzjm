"""仓库 CI 用的后端冒烟测试（依赖外网的用例非致命）。"""

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "app"))
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(prefix="qqm_smoke_")
os.environ["QQMUSIC_DATA_DIR"] = tmp
os.environ["QQMUSIC_UI_DIR"] = os.path.join(ROOT, "ui")

from fastapi.testclient import TestClient  # noqa: E402

import web.main as main  # noqa: E402

ok = 0
fail = 0


def check(name, fn, optional=False):
    """执行单项检查；optional=True 时失败只告警（如依赖外网的用例）。"""
    global ok, fail
    try:
        result = fn()
        print(f"PASS {name}: {str(result)[:200]}")
        ok += 1
    except Exception as exc:  # noqa: BLE001
        tag = "WARN" if optional else "FAIL"
        print(f"{tag} {name}: {type(exc).__name__}: {exc}")
        if not optional:
            fail += 1


with TestClient(main.app) as client:
    check("status", lambda: client.get("/api/status").json())
    check("settings", lambda: client.get("/api/settings").json())
    check("tasks", lambda: client.get("/api/tasks").json())
    check("history", lambda: client.get("/api/history").json())
    check("index", lambda: (client.get("/").status_code, len(client.get("/").text)))
    check("static-css", lambda: client.get("/static/style.css").status_code)
    check("static-app-js", lambda: client.get("/static/app.js").status_code)
    check("static-sdk", lambda: client.get("/static/js/trim-web-app.js").status_code)
    check("gateway-prefix-index", lambda: client.get("/app/qqmusic-downloader/").status_code)
    check(
        "gateway-prefix-static",
        lambda: client.get("/app/qqmusic-downloader/static/style.css").status_code,
    )
    # 网关会带前缀透传到应用 socket，API 必须在前缀下同样可用
    check("gateway-prefix-api-health", lambda: client.get("/app/qqmusic-downloader/api/health").json())
    check(
        "gateway-prefix-api-settings",
        lambda: client.get("/app/qqmusic-downloader/api/settings").json(),
    )
    check("gateway-prefix-api-tasks", lambda: client.get("/app/qqmusic-downloader/api/tasks").json())
    check("fav-without-login", lambda: client.get("/api/user/fav").json())
    check("bad-task-create", lambda: client.post("/api/tasks", json={"songs": []}).status_code)
    check("retry-unknown", lambda: client.post("/api/tasks/nope/retry").json())
    check(
        "search-network",
        lambda: client.get("/api/search", params={"keyword": "周杰伦", "num": 2}).json(),
        optional=True,
    )

print(f"SMOKE RESULT ok={ok} fail={fail}")
sys.exit(1 if fail else 0)
