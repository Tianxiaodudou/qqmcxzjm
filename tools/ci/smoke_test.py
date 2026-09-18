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





def _retire_case():

    """v1.1.21：下载完成后任务退出「下载任务」，记录进「下载历史」。"""

    from web import store as _store

    from web.downloader import DownloadManager, TASK_SUCCESS

    mgr = DownloadManager(None)

    task = mgr.submit({'songmid': 'ci001', 'name': 'CI-retire', 'singer': 'x'})

    task.status = TASK_SUCCESS

    mgr._save()

    _store.append_history(mgr._history_record(task))

    mgr._retire(task.id)

    left = [t['songmid'] for t in mgr.list_tasks()]

    hist = [h['songmid'] for h in _store.load_history()]

    assert left == [], left

    assert 'ci001' in hist, hist

    return (left, hist)





def _retire_keeps_unfinished_case():

    """失败/进行中的任务必须留在列表里（可重试）。"""

    from web.downloader import DownloadManager

    mgr = DownloadManager(None)

    bad = mgr.submit({'songmid': 'ci002', 'name': 'CI-fail', 'singer': 'x'})

    bad.status = 'failed'

    mgr._retire(bad.id)

    left = [t['songmid'] for t in mgr.list_tasks()]

    assert left == ['ci002'], left

    return left





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

    check(

        "settings-select-max-default",

        lambda: client.get("/api/settings").json()["settings"]["select_max"],

    )

    check(

        "settings-select-max-clamp",

        lambda: (

            client.post("/api/settings", json={"select_max": 99999}).json()["settings"]["select_max"],

            client.post("/api/settings", json={"select_max": 1}).json()["settings"]["select_max"],

            client.post("/api/settings", json={"select_max": 800}).json()["settings"]["select_max"],

        ),

    )

    # 首页推荐数量上限：默认值 + 越界收敛（下限由 QQ 服务器决定，这里只存上限）
    check(
        "settings-home-limits-default",
        lambda: [
            client.get("/api/settings").json()["settings"][key]
            for key in ("home_songlists_max", "home_newsongs_max", "home_guess_max", "home_radar_max")
        ],
    )
    check(
        "settings-home-limits-clamp",
        lambda: [
            client.post("/api/settings", json={"home_songlists_max": 999}).json()["settings"]["home_songlists_max"],
            client.post("/api/settings", json={"home_songlists_max": 0}).json()["settings"]["home_songlists_max"],
            client.post("/api/settings", json={"home_guess_max": 999}).json()["settings"]["home_guess_max"],
            client.post("/api/settings", json={"home_radar_max": 0}).json()["settings"]["home_radar_max"],
            client.post("/api/settings", json={"home_songlists_max": 20, "home_newsongs_max": 30,
                                              "home_guess_max": 30, "home_radar_max": 50}).json()["settings"][
                "home_radar_max"],
        ],
    )
    check("bad-task-create", lambda: client.post("/api/tasks", json={"songs": []}).status_code)

    check("retry-unknown", lambda: client.post("/api/tasks/nope/retry").json())

    check(

        "search-network",

        lambda: client.get("/api/search", params={"keyword": "周杰伦", "num": 2}).json(),

        optional=True,

    )

    # 猜你喜欢 / 每日推荐（私人雷达）：由 QQ音乐服务器按账号推送，匿名也可用

    check(

        "recommend-guess-network",

        lambda: len(client.get("/api/recommend/guess", params={"limit": 15}).json()["items"]),

        optional=True,

    )

    check(

        "recommend-radar-network",

        lambda: len(client.get("/api/recommend/radar", params={"limit": 30}).json()["items"]),

        optional=True,

    )



    # v1.1.21：完成任务退休（移出任务列表 -> 下载历史）

    check(
        "album-songs-network",
        lambda: len(
            client.get(
                "/api/album/songs",
                params={
                    "album_mid": client.get("/api/album/new", params={"limit": 1}).json()["albums"][0]["album_mid"],
                    "limit": 30,
                },
            ).json()["songs"]
        ),
        optional=True,
    )
    check("task-retire-on-success", _retire_case)

    check("task-retire-keeps-unfinished", _retire_keeps_unfinished_case)



    # v1.3.2：推送去重按事件分别设置（0 = 该类不去重，越界收敛到 1440）
    def _dedup_settings_case():
        client.post(
            "/api/settings",
            json={
                "push_dedup_success_minutes": 0,
                "push_dedup_dup_minutes": 30,
                "push_dedup_fail_minutes": 99999,
                "push_dedup_expire_minutes": 5,
            },
        )
        got = client.get("/api/settings").json()["settings"]
        assert got["push_dedup_success_minutes"] == 0, got
        assert got["push_dedup_dup_minutes"] == 30, got
        assert got["push_dedup_fail_minutes"] == 1440, got
        assert got["push_dedup_expire_minutes"] == 5, got
        return [got[k] for k in sorted(k for k in got if k.startswith("push_dedup_"))]

    check("settings-push-dedup-per-event", _dedup_settings_case)

    # v1.3.2：下载目录可「移除」（隐藏）/「恢复」，当前目录不允许移除
    def _dir_hidden_case():
        base = tempfile.mkdtemp(prefix="qqm_dirs_")
        keep = os.path.join(base, "keep")
        drop = os.path.join(base, "drop")
        os.makedirs(keep, exist_ok=True)
        os.makedirs(drop, exist_ok=True)
        for target in (keep, drop, keep):
            client.post("/api/settings", json={"download_dir": target, "dir_from_picker": True})
        assert client.post("/api/settings", json={"dir_hidden_add": drop}).status_code == 200
        data = client.get("/api/settings").json()
        assert drop in data["hidden_dirs"], data["hidden_dirs"]
        assert drop not in data["authorized_dirs"], data["authorized_dirs"]
        blocked = client.post("/api/settings", json={"dir_hidden_add": keep})
        assert blocked.status_code == 400, blocked.text
        client.post("/api/settings", json={"dir_hidden_remove": drop})
        assert drop not in client.get("/api/settings").json()["hidden_dirs"]
        # 重新选中该目录时自动解除隐藏
        client.post("/api/settings", json={"dir_hidden_add": drop})
        client.post("/api/settings", json={"download_dir": drop, "dir_from_picker": True})
        assert drop not in client.get("/api/settings").json()["hidden_dirs"]
        return 4

    check("settings-dir-hidden-restore", _dir_hidden_case)

    # v1.3.2：会员剩余时长要能认多种到期字段（秒/毫秒时间戳、日期字符串）
    def _vip_summary_case():
        import time as _time

        from web.service import QQService

        stamp = int(_time.time()) + 86400 * 100
        expect = _time.strftime("%Y-%m-%d", _time.localtime(stamp))
        seconds = QQService._vip_summary(
            {"identity": {"huge_vip": 1, "level": 8}, "userinfo": {"expire": stamp}}
        )
        assert seconds["vip_expire"] == expect, seconds
        assert seconds["vip_days_left"] in (99, 100), seconds
        assert "豪华绿钻" in seconds["vip_level"], seconds
        millis = QQService._vip_summary(
            {"identity": {"huge_vip_end": str(stamp * 1000)}, "userinfo": {"expire": 0}}
        )
        assert millis["vip_expire"] == expect, millis
        bydate = QQService._vip_summary({"identity": {"twelve_end": "2031-05-06"}})
        assert bydate["vip_expire"] == "2031-05-06", bydate
        assert bydate["vip_days_left"] > 0, bydate
        junk = QQService._vip_summary({"identity": {"vip": 1, "huge_vip_end": "abc"}})
        assert junk["vip_expire"] == "" and junk["vip_days_left"] == 0, junk
        past = QQService._vip_summary({"identity": {"vip": 1, "huge_vip_end": "2001-01-01"}})
        assert past["vip_expire"] == "2001-01-01" and past["vip_days_left"] == 0, past
        return 5

    check("vip-summary-expire-fields", _vip_summary_case)


print(f"SMOKE RESULT ok={ok} fail={fail}")

sys.exit(1 if fail else 0)

